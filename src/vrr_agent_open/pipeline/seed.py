"""Synthetic VRR data generator + loader (`make seed`), in the shape of the production
data model (see [docs/vrr_data_model.md](../../../docs/vrr_data_model.md)).

What it writes:
  vrr_raw.production_volumes_daily        allocated daily volumes per COMPLETION only
  vrr_raw.pattern                         the pattern registry
  vrr_raw.pattern_contribution_factor     completion→pattern allocation, time-windowed
  vrr_raw.pattern_pressure                pattern datum pressure, time-windowed
  vrr_raw.completion_pvt_characteristics  lab PVT per (completion, test_date, pressure)
  vrr_raw.pattern_target                  per-pattern target VRR (local addition)
  vrr_agent.pattern_memory/safety_limits/adjustment_history  agent seeds

Then it runs the deterministic builder (:mod:`vrr_agent_open.pipeline.build`) so
`vrr_curated` is computed by ``core.physics``, never invented here.

Two halves, deliberately separated:
  * :func:`generate_raw` is **pure** (no I/O, seeded RNG) so it unit-tests off-DB.
  * :func:`load` / :func:`main` do the Postgres writes.

Scenarios, chosen to light up every rule in ``core.anomaly``:
  UNITY    over-injection ramp → VRR drifts 1.00 → ~1.33 by Apr-2026
           (out_of_band + sustained_drift, with executed precedent in history)
  HORIZON  healthy, stays inside the target band (the negative control)
  MERIDIAN pattern pressure falls below its PVT test range → `extrapolated`
           lookups → any_extrapolated, so the draft is investigate-inputs
Plus a **mid-life FACTOR change** on UNITY (a completion's allocation drops at
2026-02-01), which exercises the windowed contribution-factor join.
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
    id_pattern: str
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
    PatternSpec("PAT-001", "UNITY", 3, 2, 1.0, 3200.0, -12.0, 0.030,
                (2600.0, 2900.0, 3200.0, 3500.0),
                "responds quickly to water injection cuts; watch the north injector"),
    PatternSpec("PAT-002", "HORIZON", 3, 2, 1.0, 2800.0, -6.0, 0.002,
                (2400.0, 2700.0, 3000.0), "stable; no adjustments on record"),
    PatternSpec("PAT-003", "MERIDIAN", 2, 1, 1.0, 2500.0, -45.0, 0.010,
                (2300.0, 2600.0, 2900.0), "sparse PVT coverage; audit inputs first"),
)

# One PVT test campaign per completion, at the start of history (a second campaign would
# simply add another test_date; the builder picks the latest test on or before the date).
PVT_TEST_DATE = dt.date(2025, 7, 1)

# A mid-life allocation change, to exercise the windowed factor join.
FACTOR_CHANGE = {"completion": "PAT-001-P2", "id_pattern": "PAT-001",
                 "effect_date": dt.date(2026, 2, 1), "new_factor": 0.30}


@dataclass
class RawData:
    """The six `vrr_raw` tables, as row tuples ready for `executemany`."""
    production_volumes_daily: list = field(default_factory=list)
    pattern: list = field(default_factory=list)
    pattern_contribution_factor: list = field(default_factory=list)
    pattern_pressure: list = field(default_factory=list)
    completion_pvt_characteristics: list = field(default_factory=list)
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
        raw.pattern.append((spec.id_pattern, spec.pattern_name))
        raw.pattern_target.append((spec.id_pattern, spec.target_vrr))
        producers = [f"{spec.id_pattern}-P{i+1}" for i in range(spec.n_producers)]
        injectors = [f"{spec.id_pattern}-I{i+1}" for i in range(spec.n_injectors)]

        # --- PVT: one lab curve per completion, sampled at the spec's test pressures.
        # Bo/Bw/Bg/Rs move in the usual directions with pressure so interpolation is
        # meaningful. Rv = 0 (no volatile-oil variant in this synthetic field).
        pvt_points: dict[str, list] = {}
        for cid in producers + injectors:
            jitter = 1.0 + rng.uniform(-0.02, 0.02)
            for p in spec.pvt_pressures:
                bo = round((1.18 + 0.00004 * (p - 2500.0)) * jitter, 5)
                bw = round(1.02 * jitter, 5)
                bg = round(0.00090 * (2500.0 / p), 5)
                rs = round(520.0 + 0.09 * (p - 2500.0), 3)
                bw_inj, bg_inj, rv = round(1.01 * jitter, 5), bg, 0.0
                raw.completion_pvt_characteristics.append(
                    (cid, PVT_TEST_DATE, p, bo, bg, bw, bg_inj, bw_inj, rs, rv))
                pvt_points.setdefault(cid, []).append(physics.PVTPoint(
                    pressure_psi=p, bo=bo, bw=bw, bg=bg, rs=rs, rv=rv,
                    bw_inj=bw_inj, bg_inj=bg_inj))

        # --- pattern pressure: one reading per month, valid until the next one.
        pressures = []
        for i, m in enumerate(months):
            psi = round(spec.pressure_start + spec.pressure_slope * i
                        + rng.uniform(-8, 8), 1)
            pressures.append(psi)
            raw.pattern_pressure.append((spec.id_pattern, m, psi))

        # --- contribution factors: one window opening before history starts, plus the
        # mid-life change on UNITY's P2 (two windows for that completion).
        base_oil = {c: rng.uniform(180, 320) for c in producers}
        base_water = {c: rng.uniform(400, 900) for c in producers}
        base_gas = {c: rng.uniform(90, 180) for c in producers}          # KSCF
        base_inj = {c: rng.uniform(1400, 2200) for c in injectors}
        factor = {c: round(rng.uniform(0.4, 1.0), 2) for c in producers + injectors}
        for cid in producers + injectors:
            raw.pattern_contribution_factor.append(
                (cid, spec.id_pattern, factor[cid], start - dt.timedelta(days=1)))
        if spec.id_pattern == FACTOR_CHANGE["id_pattern"]:
            raw.pattern_contribution_factor.append(
                (FACTOR_CHANGE["completion"], spec.id_pattern,
                 FACTOR_CHANGE["new_factor"], FACTOR_CHANGE["effect_date"]))

        # Calibrate month-0 injection so the pattern STARTS at its target VRR — the
        # drift afterwards is then the spec's ramp, not an artifact of the random draw.
        scale = _calibrate_injection(
            producers=producers, injectors=injectors, pvt_points=pvt_points,
            pressure=pressures[0], factor=factor, base_oil=base_oil,
            base_water=base_water, base_gas=base_gas, base_inj=base_inj,
            start_vrr=spec.target_vrr)
        base_inj = {c: v * scale for c, v in base_inj.items()}

        # --- daily volumes, keyed by COMPLETION only (no pattern column).
        for i, m in enumerate(months):
            decline = 0.995 ** i                       # ~0.5%/month production decline
            ramp = (1.0 + spec.inj_ramp) ** i
            for day in range(_days_in_month(m)):
                d = m + dt.timedelta(days=day)
                for c in producers:
                    n = lambda: 1.0 + rng.uniform(-0.05, 0.05)   # noqa: E731 daily noise
                    raw.production_volumes_daily.append((
                        c, d,
                        round(base_oil[c] * decline * n(), 3),
                        round(base_water[c] * decline * n(), 3),
                        round(base_gas[c] * decline * n(), 3),
                        0.0, 0.0, "OilField"))
                for c in injectors:
                    raw.production_volumes_daily.append((
                        c, d, 0.0, 0.0, 0.0,
                        round(base_inj[c] * ramp * (1.0 + rng.uniform(-0.04, 0.04)), 3),
                        0.0, "OilField"))
    return raw


# ---------------------------------------------------------------------------
# agent-schema seeds: safety limits bound recommendations, and one executed
# adjustment gives core.recommend.find_precedent something to cite.
# ---------------------------------------------------------------------------

def agent_rows() -> dict[str, list[tuple]]:
    memory = [(s.id_pattern, s.pattern_name, None, None, 0.90, 1.10, 1.0, 0, s.tendencies)
              for s in PATTERNS]
    limits = []
    for s in PATTERNS:
        for i in range(s.n_injectors):
            limits.append((s.id_pattern, f"{s.id_pattern}-I{i+1}", 0.15, 3800.0, 0.72,
                           "prototype limit — replace with the field's completion limits"))
    history = [(
        "ACT-2025-11-UNITY", "PAT-001", "UNITY", dt.date(2025, 11, 1),
        "res_water_inj_volume_bbl", "out_of_band", "reduce_injection", -1800.0, -0.10,
        1.18, 1.06, 1.08, "approved", "reservoir.manager", "executed")]
    return {"pattern_memory": memory, "safety_limits": limits,
            "adjustment_history": history}


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

_RAW_INSERTS = {
    "production_volumes_daily":
        "INSERT INTO vrr_raw.production_volumes_daily (id_completion, prod_date,"
        " alloc_oil_vol_stb, alloc_water_vol_stb, alloc_gas_vol_kscf,"
        " alloc_water_inj_vol_stb, alloc_gas_inj_vol_kscf, uom)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
    "pattern":
        "INSERT INTO vrr_raw.pattern (id_pattern, pattern_name) VALUES (%s,%s)",
    "pattern_contribution_factor":
        "INSERT INTO vrr_raw.pattern_contribution_factor (id_completion, id_pattern,"
        " factor, effect_date) VALUES (%s,%s,%s,%s)",
    "pattern_pressure":
        "INSERT INTO vrr_raw.pattern_pressure (id_pattern, pressure_date, pressure)"
        " VALUES (%s,%s,%s)",
    "completion_pvt_characteristics":
        "INSERT INTO vrr_raw.completion_pvt_characteristics (id_completion, test_date,"
        " pressure, bo, bg, bw, bg_inj, bw_inj, rs, rv)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "pattern_target":
        "INSERT INTO vrr_raw.pattern_target (id_pattern, target_vrr) VALUES (%s,%s)",
}

_AGENT_INSERTS = {
    "pattern_memory":
        "INSERT INTO vrr_agent.pattern_memory (id_pattern, pattern_name, latest_vrr,"
        " latest_date, typical_low, typical_high, response_factor, n_adjustments, tendencies)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id_pattern) DO UPDATE SET"
        " pattern_name=EXCLUDED.pattern_name, tendencies=EXCLUDED.tendencies",
    "safety_limits":
        "INSERT INTO vrr_agent.safety_limits (id_pattern, id_completion,"
        " max_inj_rate_change_pct, max_inj_pressure, fracture_gradient, note)"
        " VALUES (%s,%s,%s,%s,%s,%s)",
    "adjustment_history":
        "INSERT INTO vrr_agent.adjustment_history (action_id, id_pattern, pattern_name,"
        " vrr_date, driver, anomaly, change_type, d_inj_res_bbl, d_surface_pct, pre_vrr,"
        " predicted_post_vrr, actual_post_vrr, decision, approved_by, outcome)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
}


def load(raw: RawData, conn) -> dict[str, int]:
    """Replace `vrr_raw` + the seeded `vrr_agent` tables with this synthetic field."""
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("TRUNCATE vrr_raw.production_volumes_daily, vrr_raw.pattern,"
                    " vrr_raw.pattern_contribution_factor, vrr_raw.pattern_pressure,"
                    " vrr_raw.completion_pvt_characteristics, vrr_raw.pattern_target")
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
            UPDATE vrr_agent.pattern_memory m SET latest_vrr = s.vrr_bblbbl,
                   latest_date = s.vrr_date, updated_at = now()
            FROM (SELECT DISTINCT ON (id_pattern) id_pattern, vrr_date, vrr_bblbbl
                    FROM vrr_curated.pattern_vrr WHERE grain = 'monthly'
                   ORDER BY id_pattern, vrr_date DESC) s
            WHERE m.id_pattern = s.id_pattern
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
