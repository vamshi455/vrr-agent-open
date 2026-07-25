"""The deterministic analysis the agent narrates — one pattern, one period.

:func:`analyze` runs the full investigation an analyst asks for when a VRR looks
wrong, in the order the trust model demands:

  1. VERIFY the number      recompute it from raw with ``core.physics`` (VRR_AUDIT)
                            and check the PVT method — never explain a number that
                            hasn't been proven correct first
  2. EXPLAIN the change     exact ΔVRR attribution vs the prior period (core.decompose)
  3. CLASSIFY               deterministic anomaly rules (core.anomaly)
  4. PROPOSE                bounded, ρ-calibrated valve change (core.recommend) with
                            precedent — and an investigate-inputs draft instead when
                            the inputs are suspect (guardrail §6)
  5. DRAFT                  an action_queue row for human approval (core.anomaly.build_draft)

Every number in the returned ``narrative`` came out of steps 1–4. The LLM layer in
:mod:`vrr_agent_open.agent.graph` may only rephrase this; ``core.faithfulness``
checks it against ``facts`` and the decomposition before an analyst sees it.
"""
from __future__ import annotations

from ..config import TARGET_BAND
from ..core import anomaly as AN
from ..core import decompose as DC
from . import tools as T
from . import tracing


def _prev_month(date: str) -> str:
    y, m, _ = (int(x) for x in str(date).split("-"))
    return f"{y-1}-12-01" if m == 1 else f"{y}-{m-1:02d}-01"


@tracing.trace("analyst.analyze", span_type="CHAIN")
def analyze(pattern: str, date: str | None = None) -> dict:
    """Full case file for ``pattern`` at ``date`` (default: latest period)."""
    ctx = T.pattern_context(pattern)
    if not ctx.get("found"):
        return {"ok": False, "reason": f"unknown pattern '{pattern}'"}
    pid, name = ctx["pattern_id"], ctx["pattern_name"]

    history = T.vrr_trend(pid)["rows"]
    if not history:
        return {"ok": False, "reason": f"no VRR history for {name}"}
    date = date or str(history[-1]["vrr_date"])
    period = T.vrr_get(pid, date)
    if not period.get("found"):
        return {"ok": False, "reason": f"no VRR row for {name} on {date}"}

    mem = ctx.get("memory") or {}
    target = ctx["target_vrr"]
    band = ((mem.get("typical_low"), mem.get("typical_high"))
            if mem.get("typical_low") and mem.get("typical_high") else TARGET_BAND)
    vrr = period["vrr"]

    # 1 — verify before explaining
    audit = T.vrr_audit(pid, date)

    # 2 — attribute the change vs the prior period
    prior = _prev_month(date)
    decomp = T.vrr_decompose(pid, prior, date) if any(
        str(r["vrr_date"]) == prior for r in history) else {
            "ok": False, "reason": "no prior period to compare against"}

    # 3 — classify
    anomalies = AN.detect_anomalies(
        [r for r in history if str(r["vrr_date"]) <= date],
        target_vrr=target, band=band)

    # 4 — propose (only the deterministic engine may size a change)
    rec = T.recommend_change(pid, date)
    driver = decomp.get("dominant_driver") if decomp.get("ok") else None
    precedent = T.find_precedent(pid, driver=driver)
    precedent = precedent if precedent.get("found") else None

    # 5 — draft for approval, anchored on the most severe anomaly
    draft = None
    if anomalies:
        primary = sorted(anomalies, key=lambda a: {"high": 0, "medium": 1, "low": 2}[a.severity])[0]
        draft = AN.build_draft(
            pattern_id=pid, pattern_name=name, anomaly=primary, recommendation=rec,
            driver=driver, precedent=precedent,
            response_factor=mem.get("response_factor") or 1.0,
            n_adjustments=mem.get("n_adjustments") or 0)

    verdict = ("on target" if band[0] <= vrr <= band[1] else
               "OVER-replicating (injecting too much)" if vrr > band[1] else
               "UNDER-replicating (injecting too little)")

    case = {
        "ok": True, "pattern_id": pid, "pattern_name": name, "vrr_date": date,
        "vrr": vrr, "target_vrr": target, "band": list(band), "verdict": verdict,
        "period": period, "audit": audit, "decompose": decomp,
        "anomalies": [a.__dict__ for a in anomalies], "recommendation": rec,
        "precedent": precedent, "draft": draft, "memory": mem,
        "safety_limits": ctx["safety_limits"],
    }
    case["facts"] = _facts(case)
    case["narrative"] = narrate(case)
    return case


def _facts(case: dict) -> list[float]:
    """Every number the narration is allowed to contain (core.faithfulness input)."""
    out = [case["vrr"], case["target_vrr"], *case["band"]]
    a = case.get("audit") or {}
    if a.get("ok"):
        out += [a["recomputed"]["vrr"], a["recomputed"]["prod_res_bbl"],
                a["recomputed"]["inj_res_bbl"]]
    d = case.get("decompose") or {}
    if d.get("ok"):
        out += [d["vrr_a"], d["vrr_b"], d["d_vrr"]]
        out += [v for v in d["contributions"].values()]
        out += [dr["share"] * 100 for dr in d["drivers"]]
    r = case.get("recommendation") or {}
    if r.get("ok"):
        out += [r.get("current_vrr") or 0.0, r.get("expected_post_vrr") or 0.0,
                r.get("response_factor") or 1.0]
        for c in r.get("injector_changes") or []:
            out += [c["current_surface"], c["new_surface"], c["delta_surface"],
                    c["change_pct"] * 100, abs(c["change_pct"] * 100)]
    return [float(x) for x in out if x is not None]


def narrate(case: dict) -> str:
    """Deterministic narrative — the answer shown when no LLM is available, and the
    ground truth the LLM's phrasing is checked against."""
    L: list[str] = []
    L.append(f"**{case['pattern_name']} ({case['pattern_id']}) — {case['vrr_date']}**")
    L.append(f"VRR **{case['vrr']:.3f}** vs target {case['target_vrr']:.2f} "
             f"(normal band {case['band'][0]:.2f}–{case['band'][1]:.2f}) → {case['verdict']}.")

    a = case.get("audit") or {}
    if a.get("ok"):
        L.append("")
        L.append("**1. Is the number right?**")
        mark = "✅ matches" if a["matches"] else "⚠️ MISMATCH"
        L.append(f"Recomputed from {a['n_raw_rows']} raw daily rows with core.physics: "
                 f"{a['recomputed']['vrr']:.3f} vs stored {a['stored']['vrr']:.3f} — {mark} "
                 f"(diff {a['difference']:+.2e}).")
        L.append(f"PVT lookups used: {', '.join(a['pvt_methods'])}."
                 + (" ⚠️ Low-confidence PVT — inputs are suspect, so no valve change "
                    "should be made on this period." if a["low_confidence_inputs"] else ""))
        L.append(f"Sources: {', '.join(a['provenance']['recomputed_from'])} → "
                 f"vrr_curated.completion_contrib → vrr_curated.pattern_vrr_monthly "
                 f"(run_id {a['stored'].get('run_id')}).")

    d = case.get("decompose") or {}
    if d.get("ok"):
        L.append("")
        L.append(f"**2. Why did it move?** {d['date_a']} → {d['date_b']}: "
                 f"VRR {d['vrr_a']:.3f} → {d['vrr_b']:.3f} ({d['d_vrr']:+.3f})")
        for dr in d["drivers"]:
            if abs(dr["contribution"]) < 1e-9:
                continue
            # both numbers matter: the term's own move, and what it did to VRR
            # (a production term that FALLS pushes VRR up — opposite signs)
            L.append(f"- {dr['label']}: {dr['delta']:+,.0f} res bbl → "
                     f"{dr['contribution']:+.4f} VRR ({dr['share']*100:.1f}% of the move)")
        L.append(f"Attribution is exact (Σ contributions = ΔVRR) via "
                 f"{d['method']['side_split']}; term split "
                 f"inj={d['method']['injection']}, prod={d['method']['production']}.")
    elif d.get("reason"):
        L.append("")
        L.append(f"**2. Why did it move?** {d['reason']}.")

    if case["anomalies"]:
        L.append("")
        L.append("**3. Deterministic rules fired:**")
        for an in case["anomalies"]:
            L.append(f"- [{an['severity']}] {an['kind']}: {an['detail']}")
    else:
        L.append("")
        L.append("**3. Deterministic rules:** none fired — pattern is behaving normally.")

    r = case.get("recommendation") or {}
    L.append("")
    L.append("**4. Recommended action**")
    if not r.get("ok"):
        L.append(f"No recommendation: {r.get('reason')}")
    elif any(an["actionable"] is False and an["kind"] == "extrapolated_pvt"
             for an in case["anomalies"]):
        L.append("Inputs failed the audit (extrapolated PVT) — investigate source data. "
                 "No valve change proposed (guardrail: never act on suspect inputs).")
    elif r.get("direction") == "none":
        L.append(r.get("note", "Within tolerance; no change."))
    else:
        verb = "reduce" if r["direction"] == "reduce_injection" else "increase"
        L.append(f"{verb.capitalize()} injection — ρ={r['response_factor']:.2f} "
                 f"(learned from {case['memory'].get('n_adjustments', 0)} prior adjustments), "
                 f"clamped by vrr_agent.safety_limits:")
        for c in r["injector_changes"]:
            L.append(f"- {c['completion_id']}: {c['current_surface']:.0f} → "
                     f"{c['new_surface']:.0f} bbl ({c['change_pct']*100:+.1f}%"
                     + (" — CLAMPED by safety limit" if c["bounded"] else "") + ")")
        L.append(f"Expected post-VRR **{r['expected_post_vrr']:.3f}** "
                 f"(from {r['current_vrr']:.3f}, target {r['target_vrr']:.2f}).")
        if r.get("any_bounded"):
            L.append("Safety-bounded: will not fully reach target this cycle — escalate "
                     "if a larger change is needed.")
    if case.get("precedent"):
        L.append(f"Precedent: {case['precedent']['summary']}")

    L.append("")
    L.append("_Advisory only. Submitting queues a draft for analyst → RM → site approval._")
    return "\n".join(L)
