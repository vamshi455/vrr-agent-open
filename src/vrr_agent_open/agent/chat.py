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
from . import llm
from . import tools as T
from . import tracing

CFG = load_config()

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

INTENTS = (
    ("lineage", ("lineage", "calculat", "derive", "derivation", "which tables",
                 "what tables", "where does", "where do", "provenance", "trace",
                 "how do you compute", "how is it computed", "formula", "built from")),
    ("audit", ("audit", "verify", "correct", "accurate", "double check", "double-check",
               "prove", "sanity check", "recompute", "reconcile", "trust", "right?")),
    # knowledge BEFORE recommend: "what do the documents say about changing injection"
    # is a document question, not a request for a valve change.
    ("knowledge", ("document", "handbook", "literature", "paper", "manual", "guideline",
                   "guidance", "policy", "procedure", "sop", "standard", "reference say",
                   "knowledge base", "what does the doc")),
    ("recommend", ("recommend", "what should", "fix", "valve", "adjust", "change injection",
                   "action", "remediate")),
    ("submit", ("submit", "send for approval", "queue it", "raise a draft", "send to rm",
                "approval")),
    ("completions", ("completion", "wells", "well list", "which wells", "injectors",
                     "producers", "injector list")),
    ("list", ("list patterns", "which patterns", "what patterns", "all patterns")),
    ("explain", ("why", "explain", "driver", "cause", "high", "low", "increase", "decrease",
                 "drift", "what happened")),
)


# Conceptual questions ("what is VRR?", "why does over-injection matter?") that are
# about the DOMAIN rather than this field's numbers. Answered from the model's own
# knowledge (+ ingested documents when available), clearly labelled as such.
GENERAL_MARKERS = (
    "what is", "what's", "what does", "how does", "why does", "why do", "explain",
    "difference between", "should i", "should we", "best practice", "typical",
    "rule of thumb", "in general", "generally", "mean by", "definition", "concept",
    "what happens if", "how do you interpret", "good vrr", "ideal vrr",
)
DATA_MARKERS = ("this pattern", "our", "here", "shown", "screen", "table", "period")


def detect_intent(question: str) -> str:
    q = question.lower()
    for name, keys in INTENTS:
        if any(k in q for k in keys):
            return name
    return "explain"


def is_general(question: str, pattern_named: bool, date_named: bool) -> bool:
    """A conceptual question is 'general' only when it isn't pinned to our data."""
    q = question.lower()
    if pattern_named or date_named or any(m in q for m in DATA_MARKERS):
        return False
    return any(m in q for m in GENERAL_MARKERS)


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
    return llm.available(timeout)


NARRATOR_SYSTEM = (
    "You are a reservoir-engineering analyst assistant. You are given a COMPUTED "
    "analysis. Rewrite it as a clear answer to the analyst's question.\n"
    "Rules you must not break:\n"
    "- Never invent, round, or recompute a number — copy figures exactly as given.\n"
    "- Never name a driver that is not in the decomposition, and never recommend an "
    "action beyond the one computed.\n"
    "- Watch the SIGNS. Each driver line reads '<term>: <change in res bbl> → "
    "<effect on VRR>'. A NEGATIVE res bbl change means that term FELL. A production "
    "term that falls RAISES VRR (less voidage to replace); a production term that rises "
    "lowers VRR. Never write that a term increased when its res bbl change is negative.\n"
    "Keep it under 200 words."
)


def _llm_rephrase(question: str, case: dict, feedback: str | None = None) -> str | None:
    """Ask the local model to phrase the computed case. ``feedback`` re-runs it with the
    gate's complaint attached — one repair attempt before we give up on the prose."""
    messages = [{"role": "system", "content": NARRATOR_SYSTEM},
                {"role": "user", "content": f"Analyst question: {question}\n\n"
                                            f"COMPUTED ANALYSIS:\n{case['narrative']}"}]
    if feedback:
        messages.append({"role": "user", "content":
                         f"Your previous answer was rejected: {feedback} "
                         "Rewrite it, fixing that and changing nothing else."})
    try:
        return (llm.chat(messages) or {}).get("content")
    except Exception:
        return None


def _gated_answer(question: str, case: dict) -> tuple[str, dict]:
    """LLM phrasing if it survives the faithfulness gate; otherwise the computed text."""
    if not llm_available():
        return case["narrative"], {"llm": False, "gate": "skipped (no local LLM running)"}
    def _check(text: str) -> tuple[bool, dict, dict]:
        faith = FA.check_faithfulness(text, case.get("decompose"))
        nums = FA.check_numbers(text, case.get("facts") or [])
        return faith["ok"] and nums["ok"], faith, nums

    draft = _llm_rephrase(question, case)
    if not draft:
        return case["narrative"], {"llm": False, "gate": "skipped (LLM call failed)"}
    ok, faith, nums = _check(draft)
    if ok:
        return draft, {"llm": True, "gate": "passed"}

    complaint = " ".join([v["detail"] for v in faith["violations"]]
                         + ([f"These numbers came from nowhere: {nums['uncited']}."]
                            if nums["uncited"] else []))
    repaired = _llm_rephrase(question, case, feedback=complaint)
    if repaired:
        ok2, faith2, nums2 = _check(repaired)
        if ok2:
            return repaired, {"llm": True, "gate": "passed after one repair",
                              "first_attempt_violations": faith["violations"]}
        faith, nums = faith2, nums2
    return (case["narrative"],
            {"llm": True, "gate": "REJECTED — showing the computed answer instead",
             "violations": faith["violations"], "uncited_numbers": nums["uncited"]})


# ---- the router -------------------------------------------------------------

GENERAL_SYSTEM = (
    "You are a reservoir engineer explaining VRR (voidage replacement ratio) concepts to "
    "a colleague. Answer the question directly and concisely (under 200 words). Use the "
    "definitions in the primer below as authoritative — do not contradict them. You are "
    "answering from general reservoir-engineering knowledge, NOT from this site's data — "
    "if the question would need field data to answer properly, say so and name the tool "
    "or table that would provide it (vrr_curated.pattern_vrr for VRR history, "
    "vrr_curated.completion_contrib for the per-completion lineage). Never invent "
    "numbers about this field."
)


KNOWLEDGE_SYSTEM = (
    "Answer the question USING ONLY the document excerpts provided. Quote or paraphrase "
    "them; cite the file name and page for each claim. If the excerpts do not contain "
    "the answer, say so plainly — do not fall back on your own knowledge, and never "
    "state a number that is not in the excerpts."
)


def _knowledge_answer(question: str, use_llm: bool = True, k: int = 4) -> dict:
    """RAG over the pgvector index: `embedding <=> query` in Postgres, then a grounded
    summary. Retrieval is deterministic; the LLM may only summarise what came back."""
    hits = T.search_knowledge(question, k)
    if not hits.get("ok"):
        return {"intent": "knowledge", "data": hits, "meta": {"llm": False},
                "text": (f"Knowledge search is unavailable ({hits.get('reason')}).\n\n"
                         "Set it up: drop PDFs in `./knowledge_uploads/`, run "
                         "`make knowledge` to register them, approve each one in "
                         "`vrr_agent.knowledge_registry`, then run `make knowledge` "
                         "again to chunk → PII-redact → embed → store.")}
    found = hits.get("hits") or []
    if not found:
        return {"intent": "knowledge", "data": hits, "meta": {"llm": False},
                "text": "No matching chunks in the knowledge index yet."}

    sources = "\n\n".join(f"[{h['file_name']} p.{h['page']}] (similarity {h['score']:.2f})\n"
                          f"{h['text']}" for h in found)
    if not (use_llm and llm.available()):
        return {"intent": "knowledge", "data": hits, "meta": {"llm": False},
                "text": "Closest passages in the knowledge index:\n\n" + sources}

    msg = llm.chat([{"role": "system", "content": KNOWLEDGE_SYSTEM},
                    {"role": "user", "content": f"Question: {question}\n\nEXCERPTS:\n{sources}"}])
    text = (msg.get("content") or "").strip()
    cited = ", ".join(sorted({f"{h['file_name']} p.{h['page']}" for h in found}))
    return {"intent": "knowledge", "data": hits,
            "meta": {"llm": True, "model": llm.pick_model(), "retrieved": len(found),
                     "gate": "grounded in retrieved chunks (pgvector)"},
            "text": f"{text}\n\n_Sources: {cited} — retrieved from "
                    f"`vrr_agent.reservoir_knowledge` by pgvector cosine search._"}


def general_answer(question: str, use_llm: bool = True) -> dict:
    """Domain Q&A that isn't about our tables: model knowledge + any ingested documents.

    Grounded in the knowledge index when documents have been ingested (pgvector), and
    always labelled so an analyst can tell general theory from computed field results.
    """
    hits = T.search_knowledge(question, 3)
    context = ""
    if hits.get("ok") and hits.get("hits"):
        context = "\n\nExcerpts from the ingested reservoir documents:\n" + "\n".join(
            f"[{h['file_name']} p.{h['page']}] {h['text'][:600]}" for h in hits["hits"])

    if not (use_llm and llm.available()):
        return {"intent": "general", "data": hits, "meta": {"llm": False},
                "text": ("No local LLM is running, so I can only answer questions that "
                         "map onto the deterministic tools (VRR for a pattern/period, "
                         "why it moved, lineage, audit, recommendation).\n\n"
                         "Start one with `ollama serve` and pull a model "
                         "(`ollama pull qwen2.5:7b`) to ask open-ended VRR questions."
                         + (context if context else ""))}

    # Ground the model in the project's own VRR primer so a small local model can't
    # drift on the basics (e.g. inverting the ratio).
    from .graph import DOMAIN
    msg = llm.chat([{"role": "system", "content": GENERAL_SYSTEM + "\n\nPRIMER\n" + DOMAIN},
                    {"role": "user", "content": question + context}])
    text = (msg.get("content") or "").strip()
    label = ("_General reservoir-engineering knowledge"
             + (" grounded in ingested documents" if context else "")
             + " — not computed from your Postgres tables._")
    return {"intent": "general", "text": f"{text}\n\n{label}",
            "data": {"knowledge_hits": hits.get("hits", [])},
            "meta": {"llm": True, "gate": "n/a (no field figures claimed)",
                     "grounded": bool(context)}}


@tracing.trace("chat.respond", span_type="AGENT")
def respond(question: str, *, pattern: str | None = None, date: str | None = None,
            use_llm: bool = True, agentic: bool = False) -> dict:
    """Answer one analyst question. Returns text + the raw tool payloads behind it.

    ``agentic=False`` (default): the deterministic pipeline runs the tools in the right
    order and the LLM only rewrites the resulting case file — fast (~10 s) and it passes
    the gate most of the time. ``agentic=True``: the LLM drives the tool loop itself
    (`graph.run`), choosing which tables to query — slower (~1–2 min on a local 7B) and
    more likely to be caught fabricating, in which case the computed answer is shown.
    """
    intent = detect_intent(question)
    named_pattern = resolve_pattern(question, None)
    named_date = resolve_date(question, None)
    pid = named_pattern or pattern
    when = named_date or date

    if intent in ("explain", "recommend") and is_general(question, bool(named_pattern),
                                                         bool(named_date)):
        return general_answer(question, use_llm)

    if intent == "knowledge":
        return _knowledge_answer(question, use_llm)

    if intent == "list" or not pid:
        rows = T.list_patterns()
        text = "\n".join(f"- **{r['pattern_name']}** ({r['pattern_id']}) — "
                         f"{r['n']} periods, latest {r['last_date']}" for r in rows)
        return {"intent": "list", "text": "Patterns in vrr_curated:\n" + text,
                "data": {"patterns": rows}, "meta": {"llm": False}}

    if intent == "completions":
        cs = T.list_completions(pid, when)
        if not cs.get("found"):
            return {"intent": "completions", "text": "No completion rows for that period.",
                    "data": cs, "meta": {"llm": False}}
        L = [f"**{cs['pattern_name']} ({cs['pattern_id']}) — {cs['n_completions']} "
             f"completions in {cs['vrr_date']}** "
             f"({cs['n_producers']} producers, {cs['n_injectors']} injectors, "
             f"VRR {cs['vrr']:.3f})", "",
             "| completion | role | FACTOR | PVT | prod res bbl | inj res bbl | share |",
             "|---|---|---|---|---|---|---|"]
        for c_ in cs["completions"]:
            share = (c_["share_of_injection"] if c_["role"] == "injector"
                     else c_["share_of_production"])
            L.append(f"| {c_['completion_id']} | {c_['role']} | {c_['factor']:.2f} | "
                     f"{c_['pvt_methods']} | {c_['prod_res']:,.0f} | {c_['inj_res']:,.0f} | "
                     f"{share*100:.1f}% |")
        L += ["", f"_Source: `{cs['provenance']['table']}` "
                  f"(pattern {cs['pattern_id']}, month {cs['vrr_date']}); share is of the "
                  "pattern's injection total for injectors, production total for producers._"]
        return {"intent": "completions", "text": "\n".join(L), "data": cs,
                "meta": {"llm": False}}

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

    if not (use_llm and llm.available()):
        return {"intent": intent, "text": case["narrative"], "data": case,
                "meta": {"llm": False, "gate": "skipped (no local LLM running)"}}

    if not agentic:
        # Default: deterministic pipeline computed the case; the LLM only rewrites it.
        text, meta = _gated_answer(question, case)
        meta.setdefault("model", llm.pick_model())
        return {"intent": intent, "text": text, "data": case, "meta": meta}

    # Agentic: let the model drive the tool loop itself (it decides which tables to
    # query), gated. If the loop fails or the gate rejects, the computed case stands.
    from . import graph                       # local import: graph imports chat lazily
    try:
        out = graph.run(question, pattern=case["pattern_id"], date=case["vrr_date"])
    except Exception as e:
        text, meta = _gated_answer(question, case)       # fall back to rephrase-only
        meta["note"] = f"tool loop unavailable ({e})"
        return {"intent": intent, "text": text, "data": case, "meta": meta}

    gate = out.get("gate") or {}
    if not out.get("text") or not gate.get("ok"):
        # The model's narration failed verification — show the computed case file
        # instead. Being terse and right beats being fluent and wrong.
        return {"intent": intent, "text": case["narrative"],
                "data": {"case": case, "tool_trace": out.get("trace")},
                "meta": {"llm": True, "model": llm.pick_model(),
                         "gate": "REJECTED — showing the computed answer",
                         "violations": gate.get("violations"),
                         "uncited_numbers": gate.get("uncited_numbers"),
                         "tools_called": [t["tool"] for t in out.get("trace") or []]}}
    return {"intent": intent, "text": out["text"],
            "data": {"case": case, "tool_trace": out["trace"]},
            "meta": {"llm": True, "model": llm.pick_model(),
                     "gate": "passed" if gate.get("ok") else "repaired/replaced",
                     "tools_called": [t["tool"] for t in out["trace"]],
                     "violations": gate.get("violations"),
                     "uncited_numbers": gate.get("uncited_numbers")}}
