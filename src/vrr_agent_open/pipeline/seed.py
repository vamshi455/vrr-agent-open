"""Synthetic VRR data generator + loader (`make seed`).

Produces a small but *physically coherent* field so the whole stack can be exercised
offline: raw volumes/pressure/PVT/targets → `vrr_raw.*`, agent memory + safety limits
+ a little adjustment history → `vrr_agent.*`, then runs the deterministic builder
(:mod:`vrr_agent_open.pipeline.build`) to populate `vrr_curated.*` through
``core.physics``. No number is invented downstream of raw — the curated layer is
always computed.

Two halves, deliberately separated:
  * :func:`generate_raw` is **pure** (no I/O, seeded RNG) so it unit-tests off-DB,
    same convention as ``core/``.
  * :func:`load` / :func:`main` do the Postgres writes.

The scenarios are chosen to light up every deterministic rule in ``core.anomaly``:
  UNITY    over-injection ramp → VRR drifts 1.00 → 1.33 by Apr-2026
           (out_of_band + sustained_drift, with executed precedent in history)
  HORIZON  healthy, stays inside the target band (the negative control)
  MERIDIAN pattern pressure falls below its PVT test range → `extrapolated`
           lookups → any_extrapolated, so the draft is investigate-inputs
"""
from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

import psycopg

from ..config import load_config
from ..core import physics
from . import build

CFG = load_config()

SEED = 20260724                     # fixed → `make seed` is reproducible
START = dt.date(2025, 8, 1)         # 12 months of daily volumes
N_MONTHS = 12


@dataclass(frozen=True)
class PatternSpec:
    pattern_id: str
    pattern_name: str
    n_producers: int
    n_injectors: int
    target_vrr: float
    pressure_start: float
    pressure_slope: float           # psi per month (negative = depleting)
    inj_ramp: float                 # fractional injection change per month
    pvt_pressures: tuple            # measured PVT test pressures for its completions
    tendencies: str


PATTERNS = (
    # over-injecting: injection ramps +3%/month while production is flat → VRR climbs
    PatternSpec("PAT-001", "UNITY", 3, 2, 1.0, 3200.0, -12.0, 0.030,
                (2600.0, 2900.0, 3200.0, 3500.0),
                "responds quickly to water injection cuts; watch the north injector"),
    # healthy control: injection tracks production, VRR sits in the band
    PatternSpec("PAT-002", "HORIZON", 3, 2, 1.0, 2800.0, -6.0, 0.002,
                (2400.0, 2700.0, 3000.0), "stable; no adjustments on record"),
    # pressure drops below the lowest PVT test point → extrapolated lookups
    PatternSpec("PAT-003", "MERIDIAN", 2, 1, 1.0, 2500.0, -45.0, 0.010,
                (2300.0, 2600.0, 2900.0), "sparse PVT coverage; audit inputs first"),
)


@dataclass
class RawData:
    """The four `vrr_raw` tables, as plain row tuples ready for `executemany`."""
    production_volumes_daily: list = field(default_factory=list)
    pattern_pressure: list = field(default_factory=list)
    completion_pvt: list = field(default_factory=list)
    pattern_target: list = field(default_factory=list)


def _month_starts(start: dt.date, n: int) -> list[dt.date]:
    out = []
    y, m = start.year, start.month
    for _ in range(n):
        out.append(dt.date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _days_in_month(d: dt.date) -> int:
    nxt = dt.date(d.year + 1, 1, 1) if d.month == 12 else dt.date(d.year, d.month + 1, 1)
    return (nxt - d).days


def _calibrate_injection(*, producers, injectors, pvt_points, pressure, factor,
                         base_oil, base_water, base_gas, base_inj,
                         start_vrr: float) -> float:
    """Factor to scale base injection by so month-0 VRR equals ``start_vrr``.

    Runs the real ``core.physics`` terms on the noise-free base rates, so the seeded
    field starts on target regardless of what the RNG drew for rates and FACTORs.
    """
    prod_res = inj_res = 0.0
    for c in producers:
        pvt = physics.pvt_lookup(pvt_points[c], pressure)
        prod_res += physics.completion_contribution(
            factor=factor[c], oil=base_oil[c], water=base_water[c], gas=base_gas[c],
            water_inj=0.0, gas_inj=0.0, pvt=pvt.props, is_producer=True).prod_res
    for c in injectors:
        pvt = physics.pvt_lookup(pvt_points[c], pressure)
        inj_res += physics.completion_contribution(
            factor=factor[c], oil=0.0, water=0.0, gas=0.0, water_inj=base_inj[c],
            gas_inj=0.0, pvt=pvt.props, is_producer=False).inj_res
    if inj_res <= 0 or prod_res <= 0:
        return 1.0
    return start_vrr * prod_res / inj_res


def generate_raw(seed: int = SEED, n_months: int = N_MONTHS,
                 start: dt.date = START) -> RawData:
    """Build the synthetic raw field. Pure: deterministic given ``seed``."""
    rng = random.Random(seed)
    raw = RawData()
    months = _month_starts(start, n_months)

    for spec in PATTERNS:
        raw.pattern_target.append((spec.pattern_id, spec.target_vrr))
        producers = [f"{spec.pattern_id}-P{i+1}" for i in range(spec.n_producers)]
        injectors = [f"{spec.pattern_id}-I{i+1}" for i in range(spec.n_injectors)]

        # --- PVT: one lab curve per completion, sampled at the spec's test pressures.
        # Bo/Bw/Bg/Rs are pressure-dependent in the usual directions (Bg falls as
        # pressure rises; Rs rises with pressure) so interpolation is meaningful.
        pvt_points: dict[str, list] = {}
        for cid in producers + injectors:
            jitter = 1.0 + rng.uniform(-0.02, 0.02)
            for p in spec.pvt_pressures:
                row = (
                    cid, p,
                    round((1.18 + 0.00004 * (p - 2500.0)) * jitter, 5),   # bo
                    round(1.02 * jitter, 5),                              # bw
                    round(0.00090 * (2500.0 / p), 5),                     # bg  (rb/scf)
                    round(520.0 + 0.09 * (p - 2500.0), 3),                # rs  (scf/STB)
                    0.0,                                                  # rv
                    round(1.01 * jitter, 5),                              # bw_inj
                    round(0.00090 * (2500.0 / p), 5),                     # bg_inj
                )
                raw.completion_pvt.append(row)
                pvt_points.setdefault(cid, []).append(physics.PVTPoint(
                    pressure_psi=row[1], bo=row[2], bw=row[3], bg=row[4], rs=row[5],
                    rv=row[6], bw_inj=row[7], bg_inj=row[8]))

        # --- pattern pressure: one reading per month (the builder resolves the
        # latest reading on or before each production date).
        pressures = []
        for i, m in enumerate(months):
            psi = round(spec.pressure_start + spec.pressure_slope * i
                        + rng.uniform(-8, 8), 1)
            pressures.append(psi)
            raw.pattern_pressure.append((spec.pattern_id, m, psi))

        # --- daily volumes. Production declines gently; injection follows the
        # pattern's ramp, which is what drives (or doesn't drive) the VRR story.
        base_oil = {c: rng.uniform(180, 320) for c in producers}
        base_water = {c: rng.uniform(400, 900) for c in producers}
        base_gas = {c: rng.uniform(90, 180) for c in producers}          # KSCF
        base_inj = {c: rng.uniform(1400, 2200) for c in injectors}
        factor = {c: round(rng.uniform(0.4, 1.0), 2) for c in producers + injectors}

        # Calibrate month-0 injection so the pattern STARTS at its target VRR —
        # the drift afterwards is then purely the spec's ramp, not an artifact of
        # the random draw. Uses core.physics, i.e. the same math the builder runs.
        scale = _calibrate_injection(
            producers=producers, injectors=injectors, pvt_points=pvt_points,
            pressure=pressures[0], factor=factor, base_oil=base_oil,
            base_water=base_water, base_gas=base_gas, base_inj=base_inj,
            start_vrr=spec.target_vrr)
        base_inj = {c: v * scale for c, v in base_inj.items()}

        for i, m in enumerate(months):
            decline = 0.995 ** i                       # ~0.5%/month production decline
            ramp = (1.0 + spec.inj_ramp) ** i
            for day in range(_days_in_month(m)):
                d = m + dt.timedelta(days=day)
                for c in producers:
                    n = lambda: 1.0 + rng.uniform(-0.05, 0.05)   # noqa: E731 daily noise
                    raw.production_volumes_daily.append((
                        spec.pattern_id, c, d, factor[c],
                        round(base_oil[c] * decline * n(), 3),
                        round(base_water[c] * decline * n(), 3),
                        round(base_gas[c] * decline * n(), 3),
                        0.0, 0.0, "Production"))
                for c in injectors:
                    raw.production_volumes_daily.append((
                        spec.pattern_id, c, d, factor[c],
                        0.0, 0.0, 0.0,
                        round(base_inj[c] * ramp * (1.0 + rng.uniform(-0.04, 0.04)), 3),
                        0.0, "Injection"))
    return raw


# ---------------------------------------------------------------------------
# agent-schema seeds: names/band live in pattern_memory (the builder resolves
# pattern_name from it), safety limits bound recommendations, and one executed
# adjustment gives core.recommend.find_precedent something to cite.
# ---------------------------------------------------------------------------

def agent_rows() -> dict[str, list[tuple]]:
    memory = [(s.pattern_id, s.pattern_name, None, None, 0.90, 1.10, 1.0, 0, s.tendencies)
              for s in PATTERNS]
    limits = []
    for s in PATTERNS:
        for i in range(s.n_injectors):
            limits.append((s.pattern_id, f"{s.pattern_id}-I{i+1}", 0.15, 3800.0, 0.72,
                           "prototype limit — replace with the field's completion limits"))
    history = [(
        "ACT-2025-11-UNITY", "PAT-001", "UNITY", dt.date(2025, 11, 1),
        "water_inj_res", "out_of_band", "reduce_injection", -1800.0, -0.10,
        1.18, 1.06, 1.08, "approved", "reservoir.manager", "executed")]
    return {"pattern_memory": memory, "safety_limits": limits,
            "adjustment_history": history}


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

_RAW_INSERTS = {
    "production_volumes_daily":
        "INSERT INTO vrr_raw.production_volumes_daily (pattern_id, completion_id, vrr_date,"
        " factor, oil, water, gas, water_inj, gas_inj, amount_type)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "pattern_pressure":
        "INSERT INTO vrr_raw.pattern_pressure (pattern_id, vrr_date, pressure_psi)"
        " VALUES (%s,%s,%s)",
    "completion_pvt":
        "INSERT INTO vrr_raw.completion_pvt (completion_id, pressure_psi, bo, bw, bg, rs,"
        " rv, bw_inj, bg_inj) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "pattern_target":
        "INSERT INTO vrr_raw.pattern_target (pattern_id, target_vrr) VALUES (%s,%s)",
}

_AGENT_INSERTS = {
    "pattern_memory":
        "INSERT INTO vrr_agent.pattern_memory (pattern_id, pattern_name, latest_vrr,"
        " latest_date, typical_low, typical_high, response_factor, n_adjustments, tendencies)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (pattern_id) DO UPDATE SET"
        " pattern_name=EXCLUDED.pattern_name, tendencies=EXCLUDED.tendencies",
    "safety_limits":
        "INSERT INTO vrr_agent.safety_limits (pattern_id, completion_id,"
        " max_inj_rate_change_pct, max_inj_pressure, fracture_gradient, note)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
    "adjustment_history":
        "INSERT INTO vrr_agent.adjustment_history (action_id, pattern_id, pattern_name,"
        " vrr_date, driver, anomaly, change_type, d_inj_res_bbl, d_surface_pct, pre_vrr,"
        " predicted_post_vrr, actual_post_vrr, decision, approved_by, outcome)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
}


def load(raw: RawData, conn) -> dict[str, int]:
    """Replace `vrr_raw` + the seeded `vrr_agent` tables with this synthetic field."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("TRUNCATE vrr_raw.production_volumes_daily, vrr_raw.pattern_pressure,"
                    " vrr_raw.completion_pvt, vrr_raw.pattern_target")
        cur.execute("TRUNCATE vrr_agent.safety_limits, vrr_agent.adjustment_history")
        for table, sql in _RAW_INSERTS.items():
            rows = getattr(raw, table)
            cur.executemany(sql, rows)
            counts[f"vrr_raw.{table}"] = len(rows)
        for table, rows in agent_rows().items():
            cur.executemany(_AGENT_INSERTS[table], rows)
            counts[f"vrr_agent.{table}"] = len(rows)
    conn.commit()
    return counts


def refresh_memory(conn) -> int:
    """Point pattern_memory at the freshly built curated layer (latest VRR + date)."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE vrr_agent.pattern_memory m SET latest_vrr = s.vrr,
                   latest_date = s.vrr_date, updated_at = now()
            FROM (SELECT DISTINCT ON (pattern_id) pattern_id, vrr_date, vrr
                    FROM vrr_curated.pattern_vrr_monthly
                   ORDER BY pattern_id, vrr_date DESC) s
            WHERE m.pattern_id = s.pattern_id
        """)
        n = cur.rowcount
    conn.commit()
    return n


def main() -> None:
    raw = generate_raw()
    with psycopg.connect(CFG.pg_dsn) as conn:
        for t, n in load(raw, conn).items():
            print(f"  loaded {n:>6} rows → {t}")
        for t, n in build.run(conn).items():
            print(f"  built  {n:>6} rows → {t}")
        print(f"  refreshed pattern_memory for {refresh_memory(conn)} patterns")


if __name__ == "__main__":
    main()
