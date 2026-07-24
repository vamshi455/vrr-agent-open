"""Analyst chat — routes a question to deterministic tools, then (optionally) lets a
local LLM phrase the answer, behind the faithfulness gate.

Design point worth being explicit about: **the chat works with no LLM at all.** Every
answer is assembled from tool results by :mod:`vrr_agent_open.agent.analyst`; the LLM
is a *rephrasing* layer that is allowed to run only when a local Ollama endpoint is
up, and whose output is discarded if ``core.faithfulness`` rejects it. That is the
same trust model as the Databricks agent — the model never contributes a number.

    question ─▶ intent + pattern/date resolution ─▶ deterministic tools
                                                          │
                                       ┌──────────────────┴──────────────────┐
                                       │ no LLM: return the computed answer  │
                                       │ LLM up: rephrase ─▶ gate ─▶ accept  │
                                       │                     └─ reject ──────┘ (fall back)
"""
from __future__ import annotations

import calendar
import re

import httpx

from ..config import load_config
from ..core import faithfulness as FA
from . import analyst as AZ
from . import tools as T

CFG = load_config()

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

INTENTS = (
    ("lineage", ("lineage", "calculat", "derive", "derivation", "which tables",
                 "what tables", "where does", "where do", "provenance", "trace",
                 "how do you compute", "how is it computed", "formula", "built from")),
    ("audit", ("audit", "verify", "correct", "accurate", "double check", "double-check",
               "prove", "sanity check", "recompute", "reconcile", "trust", "right?")),
    ("recommend", ("recommend", "what should", "fix", "valve", "adjust", "change injection",
                   "action", "remediate")),
    ("submit", ("submit", "send for approval", "queue it", "raise a draft", "send to rm",
                "approval")),
    ("knowledge", ("document", "handbook", "literature", "paper", "manual", "guideline")),
    ("list", ("list patterns", "which patterns", "what patterns", "all patterns")),
    ("explain", ("why", "explain", "driver", "cause", "high", "low", "increase", "decrease",
                 "drift", "what happened")),
)


def detect_intent(question: str) -> str:
    q = question.lower()
    for name, keys in INTENTS:
        if any(k in q for k in keys):
            return name
    return "explain"


def resolve_pattern(question: str, default: str | None = None) -> str | None:
    """Match a pattern id/name mentioned in the question; else the UI's selection."""
    q = question.lower()
    for p in T.list_patterns():
        if (p["pattern_name"] or "").lower() in q or (p["pattern_id"] or "").lower() in q:
            return p["pattern_id"]
    return default


def resolve_date(question: str, default: str | None = None) -> str | None:
    """Parse 'April 2026' / '2026-04' / '2026-04-01' → month start. Else the default."""
    m = re.search(r"(\d{4})-(\d{2})(?:-(\d{2}))?", question)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"
    m = re.search(r"([a-z]+)\s+(\d{4})", question.lower())
    if m and m.group(1) in MONTHS:
        return f"{m.group(2)}-{MONTHS[m.group(1)]:02d}-01"
    return default


# ---- optional local LLM -----------------------------------------------------

def llm_available(timeout: float = 1.5) -> bool:
    try:
        r = httpx.get(f"{CFG.llm_base_url}/api/tags", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


NARRATOR_SYSTEM = (
    "You are a reservoir-engineering analyst assistant. You are given a COMPUTED "
    "analysis. Rewrite it as a clear answer to the analyst's question. Rules you must "
    "not break: never invent, round, or recompute a number — copy figures exactly as "
    "given; never name a driver that is not in the decomposition; never recommend an "
    "action beyond the one computed. Keep it under 200 words."
)


def _llm_rephrase(question: str, case: dict) -> str | None:
    try:
        r = httpx.post(f"{CFG.llm_base_url}/api/chat", json={
            "model": CFG.llm_model, "stream": False,
            "messages": [
                {"role": "system", "content": NARRATOR_SYSTEM},
                {"role": "user", "content": f"Analyst question: {question}\n\n"
                                            f"COMPUTED ANALYSIS:\n{case['narrative']}"}],
        }, timeout=120)
        return (r.json().get("message") or {}).get("content")
    except Exception:
        return None


def _gated_answer(question: str, case: dict) -> tuple[str, dict]:
    """LLM phrasing if it survives the faithfulness gate; otherwise the computed text."""
    if not llm_available():
        return case["narrative"], {"llm": False, "gate": "skipped (no local LLM running)"}
    draft = _llm_rephrase(question, case)
    if not draft:
        return case["narrative"], {"llm": False, "gate": "skipped (LLM call failed)"}
    faith = FA.check_faithfulness(draft, case.get("decompose"))
    nums = FA.check_numbers(draft, case.get("facts") or [])
    if faith["ok"] and nums["ok"]:
        return draft, {"llm": True, "gate": "passed", "checks": {"drivers": faith,
                                                                 "numbers": nums}}
    return (case["narrative"],
            {"llm": True, "gate": "REJECTED — showing the computed answer instead",
             "violations": faith["violations"],
             "uncited_numbers": nums["uncited"]})


# ---- the router -------------------------------------------------------------

def respond(question: str, *, pattern: str | None = None, date: str | None = None,
            use_llm: bool = True) -> dict:
    """Answer one analyst question. Returns text + the raw tool payloads behind it."""
    intent = detect_intent(question)
    pid = resolve_pattern(question, pattern)
    when = resolve_date(question, date)

    if intent == "list" or not pid:
        rows = T.list_patterns()
        text = "\n".join(f"- **{r['pattern_name']}** ({r['pattern_id']}) — "
                         f"{r['n']} periods, latest {r['last_date']}" for r in rows)
        return {"intent": "list", "text": "Patterns in vrr_curated:\n" + text,
                "data": {"patterns": rows}, "meta": {"llm": False}}

    if intent == "knowledge":
        hits = T.search_knowledge(question)
        if not hits.get("ok"):
            return {"intent": "knowledge", "data": hits, "meta": {"llm": False},
                    "text": f"Knowledge search is unavailable ({hits.get('reason')}). "
                            "Ingest PDFs with `make knowledge` and run Ollama for embeddings."}
        lines = [f"- {h['file_name']} p.{h['page']} (score {h['score']:.2f}): "
                 f"{h['text'][:240]}…" for h in hits["hits"]]
        return {"intent": "knowledge", "text": "\n".join(lines) or "No matching chunks.",
                "data": hits, "meta": {"llm": False}}

    if intent == "lineage":
        lin = T.vrr_lineage(pid, when) if when else None
        if not lin or not lin.get("found"):
            return {"intent": "lineage", "text": "Pick a period to trace (no rows found).",
                    "data": lin or {}, "meta": {"llm": False}}
        L = [f"**How {lin['pattern_name']}'s VRR for {lin['vrr_date']} was computed**", "",
             f"`{lin['formulas']['vrr']}`", "",
             f"Aggregate row: prod {lin['monthly']['prod_res_bbl']:,.0f} rb, "
             f"inj {lin['monthly']['inj_res_bbl']:,.0f} rb, VRR {lin['monthly']['vrr']:.3f} "
             f"(run_id {lin['monthly'].get('run_id')}).", "",
             f"Built from {len(lin['completions'])} completions in "
             "`vrr_curated.completion_contrib` — each row keeps its raw inputs, the pattern "
             "pressure used, the PVT lookup method, and the derived terms:"]
        for c in lin["completions"]:
            L.append(f"- **{c['completion_id']}** ({c['n_days']} days, PVT "
                     f"{c['pvt_methods']}, pressure {c['min_pressure']:.0f}–"
                     f"{c['max_pressure']:.0f} psi, FACTOR {c['factor']:.2f}): "
                     f"oil_res {c['oil_res']:,.0f} · water_res {c['water_res']:,.0f} · "
                     f"free_gas_res {c['free_gas_res']:,.0f} · water_inj_res "
                     f"{c['water_inj_res']:,.0f} rb")
        L += ["", "Chain: " + " → ".join(
            [lin["sources"]["raw_volumes"], lin["sources"]["pressure"],
             lin["sources"]["pvt"], lin["sources"]["lineage_layer"],
             lin["sources"]["aggregate"]]),
            f"Computed by `{lin['sources']['code']}`."]
        return {"intent": "lineage", "text": "\n".join(L), "data": lin,
                "meta": {"llm": False}}

    if intent == "audit":
        when = when or str(T.vrr_trend(pid)["rows"][-1]["vrr_date"])
        a = T.vrr_audit(pid, when)
        if not a.get("ok"):
            return {"intent": "audit", "text": a.get("reason", "audit failed"),
                    "data": a, "meta": {"llm": False}}
        mark = "✅ the stored number is correct" if a["matches"] else "⚠️ MISMATCH"
        text = (f"**Audit — {a['pattern_name']} {a['vrr_date']}**\n\n"
                f"Recomputed independently from {a['n_raw_rows']} raw daily rows "
                f"(`{'`, `'.join(a['provenance']['recomputed_from'])}`) through "
                f"`{a['provenance']['code']}`:\n"
                f"- recomputed VRR **{a['recomputed']['vrr']:.6f}**\n"
                f"- stored VRR **{a['stored']['vrr']:.6f}** (run_id {a['stored'].get('run_id')})\n"
                f"- difference {a['difference']:+.2e} → {mark}\n\n"
                f"PVT methods used: {', '.join(a['pvt_methods'])}."
                + ("\n\n⚠️ Low-confidence PVT (extrapolated/closest) — the inputs are "
                   "suspect even though the arithmetic is right; audit source data before "
                   "acting." if a["low_confidence_inputs"] else ""))
        return {"intent": "audit", "text": text, "data": a, "meta": {"llm": False}}

    # explain / recommend / submit all need the full case file
    case = AZ.analyze(pid, when)
    if not case.get("ok"):
        return {"intent": intent, "text": case.get("reason", "no analysis"),
                "data": case, "meta": {"llm": False}}

    if intent == "submit":
        if not case.get("draft"):
            return {"intent": "submit", "data": case, "meta": {"llm": False},
                    "text": "Nothing to submit — no anomaly fired for this period."}
        res = T.submit_for_approval(case["pattern_id"], case["vrr_date"],
                                    draft=case["draft"], submitted_by="agent-chat")
        return {"intent": "submit", "data": {"case": case, "submitted": res},
                "meta": {"llm": False},
                "text": (f"Queued **{res['action_id']}** at stage `draft` for "
                         f"{case['pattern_name']} {case['vrr_date']}. Next approver: "
                         f"**{res['next_approver']}** → RM → site. "
                         "Open the Approval queue tab to action it.")}

    text, meta = (_gated_answer(question, case) if use_llm
                  else (case["narrative"], {"llm": False, "gate": "disabled"}))
    return {"intent": intent, "text": text, "data": case, "meta": meta}
