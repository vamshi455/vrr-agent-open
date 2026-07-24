"""Off-DB tests for the synthetic field (`pipeline/seed.py`).

These never touch Postgres: ``generate_raw`` is pure, so we can re-run the builder's
math here with ``core.physics`` and assert the seeded scenarios really do produce the
VRR story the agent demo depends on (UNITY drifting out of band by Apr-2026, HORIZON
staying healthy, MERIDIAN going extrapolated as pressure falls below its PVT range).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pytest

from vrr_agent_open.config import TARGET_BAND
from vrr_agent_open.core import physics
from vrr_agent_open.pipeline import seed


def _monthly_vrr(raw: seed.RawData) -> dict[tuple[str, dt.date], dict]:
    """Re-implement the builder's aggregation in memory (same core.physics calls)."""
    pvt_points: dict[str, list[physics.PVTPoint]] = defaultdict(list)
    for cid, psi, bo, bw, bg, rs, rv, bw_inj, bg_inj in raw.completion_pvt:
        pvt_points[cid].append(physics.PVTPoint(pressure_psi=psi, bo=bo, bw=bw, bg=bg,
                                                rs=rs, rv=rv, bw_inj=bw_inj, bg_inj=bg_inj))
    pressure = {(p, d): psi for p, d, psi in raw.pattern_pressure}
    agg: dict[tuple[str, dt.date], dict] = {}
    for (pid, cid, d, factor, oil, water, gas, water_inj, gas_inj,
         amount_type) in raw.production_volumes_daily:
        month = d.replace(day=1)
        pvt = physics.pvt_lookup(pvt_points[cid], pressure[(pid, month)])
        t = physics.completion_contribution(
            factor=factor, oil=oil, water=water, gas=gas, water_inj=water_inj,
            gas_inj=gas_inj, pvt=pvt.props, is_producer=(amount_type == "Production"))
        a = agg.setdefault((pid, month), {"prod": 0.0, "inj": 0.0, "extrap": False})
        a["prod"] += t.prod_res
        a["inj"] += t.inj_res
        a["extrap"] = a["extrap"] or pvt.method in (physics.EXTRAP, physics.CLOSEST,
                                                    physics.NONE)
    for a in agg.values():
        a["vrr"] = physics.vrr(a["inj"], a["prod"])
    return agg


RAW = seed.generate_raw()
MONTHLY = _monthly_vrr(RAW)


def test_generation_is_deterministic():
    assert seed.generate_raw().production_volumes_daily == RAW.production_volumes_daily


def test_covers_every_pattern_and_month():
    months = {m for _, m in MONTHLY}
    assert len(months) == seed.N_MONTHS
    assert {p for p, _ in MONTHLY} == {s.pattern_id for s in seed.PATTERNS}
    assert min(months) == seed.START


def test_patterns_start_on_target():
    for spec in seed.PATTERNS:
        assert MONTHLY[(spec.pattern_id, seed.START)]["vrr"] == \
            pytest.approx(spec.target_vrr, rel=0.05)


def test_unity_drifts_out_of_band_by_april_2026():
    assert MONTHLY[("PAT-001", dt.date(2026, 4, 1))]["vrr"] > TARGET_BAND[1]
    series = [a["vrr"] for (p, _), a in sorted(MONTHLY.items()) if p == "PAT-001"]
    assert series == sorted(series)                 # monotone → sustained_drift fires


def test_horizon_stays_inside_the_band():
    lo, hi = TARGET_BAND
    for (pid, _), a in MONTHLY.items():
        if pid == "PAT-002":
            assert lo <= a["vrr"] <= hi


def test_meridian_goes_extrapolated_as_pressure_falls():
    series = [(m, a["extrap"]) for (p, m), a in sorted(MONTHLY.items()) if p == "PAT-003"]
    assert series[0][1] is False                    # in PVT range at the start
    assert series[-1][1] is True                    # below the lowest test point later


def test_agent_seed_rows_are_consistent():
    rows = seed.agent_rows()
    assert {r[0] for r in rows["pattern_memory"]} == {s.pattern_id for s in seed.PATTERNS}
    assert len(rows["safety_limits"]) == sum(s.n_injectors for s in seed.PATTERNS)
    assert all(0 < r[2] <= 1 for r in rows["safety_limits"])       # max change pct
    assert rows["adjustment_history"][0][-1] == "executed"         # precedent exists
