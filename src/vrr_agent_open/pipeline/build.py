"""VRR builder: `vrr_raw` → `vrr_curated` (the OSS port of the Spark/SQL builder).

Every derived number here comes from :mod:`vrr_agent_open.core.physics` — the same
pure functions the unit tests pin — so the curated layer is reproducible and the
agent only ever narrates computed values.

Two steps:
  1. ``build_contrib``  raw volumes + pattern pressure + completion PVT →
     ``vrr_curated.completion_contrib`` (the LINEAGE layer: root inputs *and* the
     derived reservoir terms *and* the PVT method label, one row per
     (pattern, completion, date)).
  2. ``build_monthly``  contrib → ``vrr_curated.pattern_vrr_monthly``, aggregating
     to month grain: VRR = Σinj_res / Σprod_res, with ``any_extrapolated`` raised
     whenever any contributing row used a low-confidence PVT lookup.

Pattern pressure is resolved as the latest reading **on or before** the production
date (no interpolation across readings — the PVT ladder already carries the
confidence label). ``pattern_name`` is resolved from ``vrr_agent.pattern_memory``,
the registry of pattern identity, falling back to ``pattern_id``.
"""
from __future__ import annotations

import uuid

import psycopg

from ..config import load_config
from ..core import physics

CFG = load_config()

# PVT method labels that mean "don't trust this row's inputs" (core.anomaly rule 1).
LOW_CONFIDENCE = (physics.EXTRAP, physics.CLOSEST, physics.NONE)

# raw volumes + the pressure in force on that date (latest reading on or before).
_VOLUMES_SQL = """
SELECT v.pattern_id, v.completion_id, v.vrr_date, v.factor,
       v.oil, v.water, v.gas, v.water_inj, v.gas_inj, v.amount_type, p.pressure_psi
  FROM vrr_raw.production_volumes_daily v
  LEFT JOIN LATERAL (
       SELECT pressure_psi FROM vrr_raw.pattern_pressure pp
        WHERE pp.pattern_id = v.pattern_id AND pp.vrr_date <= v.vrr_date
        ORDER BY pp.vrr_date DESC LIMIT 1) p ON true
 ORDER BY v.pattern_id, v.vrr_date, v.completion_id
"""

_CONTRIB_INSERT = """
INSERT INTO vrr_curated.completion_contrib
 (pattern_id, completion_id, vrr_date, factor, oil, water, gas, water_inj, gas_inj,
  pressure_psi, pvt_method, oil_res, water_res, free_gas_res, water_inj_res,
  gas_inj_res, run_id)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

_MONTHLY_INSERT = """
INSERT INTO vrr_curated.pattern_vrr_monthly
 (pattern_id, pattern_name, vrr_date, prod_res_bbl, inj_res_bbl, vrr, n_completions,
  any_extrapolated, run_id)
SELECT c.pattern_id,
       COALESCE(m.pattern_name, c.pattern_id),
       date_trunc('month', c.vrr_date)::date            AS vrr_date,
       SUM(c.oil_res + c.water_res + COALESCE(c.free_gas_res, 0))  AS prod_res_bbl,
       SUM(c.water_inj_res + c.gas_inj_res)                        AS inj_res_bbl,
       SUM(c.water_inj_res + c.gas_inj_res)
         / NULLIF(SUM(c.oil_res + c.water_res + COALESCE(c.free_gas_res, 0)), 0) AS vrr,
       COUNT(DISTINCT c.completion_id)                             AS n_completions,
       BOOL_OR(c.pvt_method = ANY(%(low)s))                        AS any_extrapolated,
       %(run_id)s
  FROM vrr_curated.completion_contrib c
  LEFT JOIN vrr_agent.pattern_memory m ON m.pattern_id = c.pattern_id
 GROUP BY c.pattern_id, COALESCE(m.pattern_name, c.pattern_id),
          date_trunc('month', c.vrr_date)
"""


def _pvt_points(conn) -> dict[str, list[physics.PVTPoint]]:
    """All lab PVT rows, grouped per completion (small table — read once)."""
    points: dict[str, list[physics.PVTPoint]] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT completion_id, pressure_psi, bo, bw, bg, rs, rv, bw_inj,"
                    " bg_inj FROM vrr_raw.completion_pvt")
        for cid, psi, bo, bw, bg, rs, rv, bw_inj, bg_inj in cur.fetchall():
            points.setdefault(cid, []).append(physics.PVTPoint(
                pressure_psi=psi, bo=bo, bw=bw, bg=bg, rs=rs, rv=rv,
                bw_inj=bw_inj, bg_inj=bg_inj))
    return points


def build_contrib(conn, run_id: str) -> int:
    """Step 1 — per-completion reservoir terms via ``core.physics`` (with lineage)."""
    pvt_by_completion = _pvt_points(conn)
    cache: dict[tuple, physics.PVTResult] = {}     # (completion, pressure) → lookup
    rows: list[tuple] = []

    with conn.cursor(name="volumes") as cur:       # server-side cursor: streams raw
        cur.execute(_VOLUMES_SQL)
        for (pattern_id, completion_id, vrr_date, factor, oil, water, gas,
             water_inj, gas_inj, amount_type, pressure_psi) in cur:
            key = (completion_id, pressure_psi)
            pvt = cache.get(key)
            if pvt is None:
                pvt = cache[key] = physics.pvt_lookup(
                    pvt_by_completion.get(completion_id, []), pressure_psi)
            terms = physics.completion_contribution(
                factor=factor, oil=oil, water=water, gas=gas, water_inj=water_inj,
                gas_inj=gas_inj, pvt=pvt.props,
                is_producer=(amount_type == "Production"))
            rows.append((pattern_id, completion_id, vrr_date, factor, oil, water, gas,
                         water_inj, gas_inj, pressure_psi, pvt.method,
                         terms.oil_res, terms.water_res, terms.free_gas_res,
                         terms.water_inj_res, terms.gas_inj_res, run_id))

    with conn.cursor() as cur:
        cur.execute("TRUNCATE vrr_curated.completion_contrib")
        cur.executemany(_CONTRIB_INSERT, rows)
    conn.commit()
    return len(rows)


def build_monthly(conn, run_id: str) -> int:
    """Step 2 — aggregate contrib to pattern × month VRR."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE vrr_curated.pattern_vrr_monthly")
        cur.execute(_MONTHLY_INSERT, {"run_id": run_id, "low": list(LOW_CONFIDENCE)})
        n = cur.rowcount
    conn.commit()
    return n


def run(conn=None, run_id: str | None = None) -> dict[str, int]:
    """Full raw → curated rebuild. Both curated tables carry the same ``run_id``."""
    run_id = run_id or uuid.uuid4().hex[:12]
    if conn is not None:
        return {"vrr_curated.completion_contrib": build_contrib(conn, run_id),
                "vrr_curated.pattern_vrr_monthly": build_monthly(conn, run_id)}
    with psycopg.connect(CFG.pg_dsn) as c:
        return run(c, run_id)


if __name__ == "__main__":
    for table, count in run().items():
        print(f"  built {count:>6} rows → {table}")
