"""Off-DB tests for the synthetic field (`pipeline/seed.py`).

These never touch Postgres: ``generate_raw`` is pure, so the builder's math is re-run
here with ``core.physics`` — including the **windowed** contribution-factor and pressure
joins, which is where the data model actually lives — and we assert the seeded scenarios
produce the VRR story the agent demo depends on.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import pytest

from vrr_agent_open.config import TARGET_BAND
from vrr_agent_open.core import physics
from vrr_agent_open.pipeline import seed


def _windows(rows: list[tuple], key_idx: tuple, date_idx: int):
    """Turn (…, effect_date) rows into half-open windows, as LEAD() does in SQL."""
    by_key = defaultdict(list)
    for r in rows:
        by_key[tuple(r[i] for i in key_idx)].append(r)
    for key, rs in by_key.items():
        rs.sort(key=lambda r: r[date_idx])
        for i, r in enumerate(rs):
            end = rs[i + 1][date_idx] if i + 1 < len(rs) else None
            yield key, r, r[date_idx], end


def _monthly_vrr(raw: seed.RawData) -> dict[tuple[str, dt.date], dict]:
    """Re-implement the builder in memory: volumes ⋈ factor window ⋈ pressure window."""
    # PVT points per (completion, test_date)
    pvt: dict[tuple, list] = defaultdict(list)
    for (cid, tdate, psi, bo, bg, bw, bg_inj, bw_inj, rs, rv) in \
            raw.completion_pvt_characteristics:
        pvt[(cid, tdate)].append(physics.PVTPoint(
            pressure_psi=psi, bo=bo, bw=bw, bg=bg, rs=rs, rv=rv,
            bw_inj=bw_inj, bg_inj=bg_inj))
    tests = sorted({k[1] for k in pvt})

    factor_windows = list(_windows(raw.pattern_contribution_factor, (0, 1), 3))
    pressure_windows = list(_windows(raw.pattern_pressure, (0,), 1))
    volumes = defaultdict(list)
    for row in raw.production_volumes_daily:
        volumes[row[0]].append(row)

    agg: dict[tuple[str, dt.date], dict] = {}
    for (cid, pid), frow, f_start, f_end in factor_windows:
        factor = frow[2]
        for (_, vdate, oil, water, gas, water_inj, gas_inj, uom) in volumes[cid]:
            if vdate < f_start or (f_end is not None and vdate >= f_end):
                continue                                    # outside this factor window
            pressure = next((p[2] for (kp,), p, s, e in pressure_windows
                             if kp == pid and s <= vdate and (e is None or vdate < e)), None)
            applicable = [d for d in tests if d <= vdate]
            tdate = applicable[-1] if applicable else tests[0]
            look = physics.pvt_lookup(pvt[(cid, tdate)], pressure)
            t = physics.completion_contribution(
                factor=factor, oil=oil, water=water, gas=gas, water_inj=water_inj,
                gas_inj=gas_inj, pvt=look.props,
                is_producer=(oil + water + gas) > 0,
                gas_kscf_to_scf=1000.0 if uom == "OilField" else 1.0)
            a = agg.setdefault((pid, vdate.replace(day=1)),
                               {"prod": 0.0, "inj": 0.0, "extrap": False})
            a["prod"] += t.prod_res
            a["inj"] += t.inj_res
            a["extrap"] = a["extrap"] or look.method in (physics.EXTRAP, physics.CLOSEST,
                                                         physics.NONE)
    for a in agg.values():
        a["vrr"] = physics.vrr(a["inj"], a["prod"])
    return agg


RAW = seed.generate_raw()
MONTHLY = _monthly_vrr(RAW)


def test_generation_is_deterministic():
    assert seed.generate_raw().production_volumes_daily == RAW.production_volumes_daily


def test_volumes_are_keyed_by_completion_not_pattern():
    """The data-model rule: a daily volume row knows nothing about patterns — membership
    comes from pattern_contribution_factor."""
    row = RAW.production_volumes_daily[0]
    assert len(row) == 8 and isinstance(row[0], str) and isinstance(row[1], dt.date)
    assert all("PAT-" in r[0] for r in RAW.production_volumes_daily)
    assert {len(r) for r in RAW.pattern_contribution_factor} == {4}


def test_covers_every_pattern_and_month():
    months = {m for _, m in MONTHLY}
    assert len(months) == seed.N_MONTHS
    assert {p for p, _ in MONTHLY} == {s.id_pattern for s in seed.PATTERNS}
    assert min(months) == seed.START


def test_patterns_start_on_target():
    for spec in seed.PATTERNS:
        assert MONTHLY[(spec.id_pattern, seed.START)]["vrr"] == \
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


def test_mid_life_factor_change_has_two_windows():
    """The allocation change must produce two windows for the same completion+pattern,
    and the second one must actually take effect (VRR steps up when production drops)."""
    rows = [r for r in RAW.pattern_contribution_factor
            if r[0] == seed.FACTOR_CHANGE["completion"]]
    assert len(rows) == 2
    assert sorted(r[3] for r in rows)[1] == seed.FACTOR_CHANGE["effect_date"]
    before = MONTHLY[("PAT-001", dt.date(2026, 1, 1))]["vrr"]
    after = MONTHLY[("PAT-001", dt.date(2026, 2, 1))]["vrr"]
    assert after - before > 0.05                    # less allocated production → higher VRR


def test_agent_seed_rows_are_consistent():
    rows = seed.agent_rows()
    assert {r[0] for r in rows["pattern_memory"]} == {s.id_pattern for s in seed.PATTERNS}
    assert len(rows["safety_limits"]) == sum(s.n_injectors for s in seed.PATTERNS)
    assert all(0 < r[2] <= 1 for r in rows["safety_limits"])       # max change pct
    assert rows["adjustment_history"][0][-1] == "executed"         # precedent exists
