"""LangGraph-shaped agent loop — the OSS equivalent of the Databricks ChatAgent.

    plan ─▶ tools ─▶ plan ─▶ … ─▶ gate ─▶ answer
      │ (LLM picks tool + args)      │ (faithfulness: drivers + numbers)
      └──────────── loop ───────────┘        └─ reject ─▶ retry once ─▶ fall back

The LLM plans and narrates. Every number comes from `tools.py` (deterministic, over
Postgres) or `core/` — and `core.faithfulness` verifies the narration against the tool
output before the analyst sees it. A rejected answer is retried once with the violation
fed back, then replaced by the computed text.

The system prompt below is where the model learns the domain: what VRR is, what the
three Postgres schemas contain, how a VRR number is derived (the lineage chain), and
which tool answers which question.

Run: `python -m vrr_agent_open.agent.graph "Why is UNITY's VRR high in April 2026?"`
"""
from __future__ import annotations

import json
import sys
from typing import Annotated, TypedDict

from ..config import load_config
from ..core import faithfulness as FA
from . import llm
from . import tools as T
from . import tracing

CFG = load_config()

DOMAIN = """You are a reservoir engineer's assistant for VRR (Voidage Replacement Ratio)
analysis on a waterflood.

DOMAIN
- VRR = injected reservoir volume / produced reservoir volume, per pattern per month.
  VRR ≈ 1 means voidage is being replaced. > 1 = over-injection (pressure build-up,
  risk of fracturing, water cycling, wasted injection). < 1 = under-injection
  (reservoir pressure decline, loss of drive energy, lost recovery).
- Surface volumes are converted to reservoir volumes with PVT properties (Bo, Bw, Bg,
  Rs) interpolated at the pattern's pressure. Terms:
    oil_res       = FACTOR · OIL · Bo
    water_res     = FACTOR · WATER · Bw
    free_gas_res  = FACTOR · (GAS·1000 − Rs·OIL) · Bg      (producers, OIL > 0)
    water_inj_res = FACTOR · WATER_INJ · Bw_inj
    gas_inj_res   = FACTOR · GAS_INJ·1000 · Bg_inj
  VRR = Σ(water_inj_res + gas_inj_res) / Σ(oil_res + water_res + free_gas_res)
- A PVT lookup is labelled exact / interpolated / extrapolated / closest / none. An
  extrapolated or closest lookup means the INPUTS are suspect: the VRR may be wrong,
  and no valve change may be recommended on that period.

DATA (PostgreSQL)
- vrr_raw.production_volumes_daily — allocated daily volumes per COMPLETION (no pattern
  column: a completion belongs to a pattern only through the contribution factor).
- vrr_raw.pattern_contribution_factor — completion→pattern FACTOR, time-windowed by
  effect_date; vrr_raw.pattern_pressure — pattern datum pressure, also time-windowed.
- vrr_raw.completion_pvt_characteristics — lab PVT per (completion, test_date, pressure).
- vrr_curated.completion_contrib — the LINEAGE layer: one row per pattern·completion·day
  holding the raw inputs, the resolved factor + pressure, the PVT method label and the
  exact FVFs used, and all five derived reservoir volumes.
- vrr_curated.pattern_vrr — the DAILY and MONTHLY pattern VRR (grain column), with
  surface + reservoir totals, vrr_bblbbl, and volume-weighted average FVFs.
- vrr_agent.pattern_memory (target band, learned response factor rho),
  safety_limits (max % injection change), adjustment_history (past executed changes),
  action_queue (drafts awaiting analyst → RM → site approval).

HOW TO WORK
1. Any figure about this field MUST come from a tool call. Never estimate, never do
   arithmetic yourself, never round a tool's number to a "nicer" one.
2. Before explaining a suspicious number, call VRR_AUDIT — it recomputes the month from
   raw data and tells you whether the stored value is right and whether the PVT inputs
   were extrapolated.
3. To say WHY VRR moved, call VRR_DECOMPOSE and name only the terms it returns, in the
   direction it reports. Its contributions sum exactly to the VRR change.
4. To propose an action, call RECOMMEND_CHANGE — the magnitude is computed from physics
   and clamped by safety limits. Never invent your own change size.
5. VRR_LINEAGE shows how a specific monthly number was built, completion by completion.
6. General reservoir-engineering questions that are not about this field's data may be
   answered from your own knowledge — say explicitly that it is general knowledge and
   not from the tables.
7. Cite the table you got each figure from. Be concise: an engineer is reading this.
8. Copy figures VERBATIM from tool results. Do NOT compute averages, per-day rates,
   sums, percentages or unit conversions yourself — if you need one, there is a tool for
   it. Do not restate long lists of raw volumes; quote only the figures your answer
   needs. Any number you write that no tool returned will be rejected by the gate.
"""


class State(TypedDict):
    messages: Annotated[list, lambda a, b: a + b]
    last_decompose: dict | None


def _numbers_in(obj, out: list[float]) -> list[float]:
    """Every numeric value a tool returned — the whitelist for check_numbers."""
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
        # also allow the same figure expressed as a percentage or rounded
        out.extend([float(obj) * 100, round(float(obj), 2), round(float(obj), 3)])
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers_in(v, out)
    return out


@tracing.trace("agent.tool_loop", span_type="AGENT")
def run(question: str, *, pattern: str | None = None, date: str | None = None,
        max_steps: int = 6, model: str | None = None) -> dict:
    """LLM tool loop → gated answer. Returns text, the tool trace, and gate verdict."""
    hint = ""
    if pattern or date:
        hint = (f"\n\nThe analyst is currently looking at "
                f"{'pattern ' + pattern if pattern else ''}"
                f"{' for period ' + date if date else ''}. Use that unless the question "
                "names another pattern or period.")
    messages: list[dict] = [{"role": "system", "content": DOMAIN + hint},
                            {"role": "user", "content": question}]
    trace: list[dict] = []
    facts: list[float] = []
    last_decompose: dict | None = None

    for _ in range(max_steps):
        msg = llm.chat(messages, tools=T.TOOL_SPECS, model=model)
        calls = msg.get("tool_calls") or []
        if not calls:
            answer = msg.get("content", "") or ""
            gated, verdict = _gate(answer, last_decompose, facts)
            if verdict["ok"] or verdict.get("retried"):
                return {"text": gated, "trace": trace, "gate": verdict,
                        "decompose": last_decompose}
            # one repair attempt, with the violation fed back to the model
            messages += [msg, {"role": "user", "content": _repair_prompt(verdict)}]
            msg2 = llm.chat(messages, tools=None, model=model)
            gated2, verdict2 = _gate(msg2.get("content", "") or "", last_decompose, facts)
            verdict2["retried"] = True
            return {"text": gated2, "trace": trace, "gate": verdict2,
                    "decompose": last_decompose}

        messages.append(msg)
        for tc in calls:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            result = T.call_tool(fn.get("name", ""), args)     # deterministic, over Postgres
            if fn.get("name") == "VRR_DECOMPOSE" and result.get("ok"):
                last_decompose = result
            _numbers_in(result, facts)
            trace.append({"tool": fn.get("name"), "args": args, "result": result})
            messages.append({"role": "tool", "name": fn.get("name"),
                             "content": json.dumps(result, default=str)[:8000]})

    return {"text": "Could not complete within the tool budget — narrow the question.",
            "trace": trace, "gate": {"ok": False, "reason": "step budget exhausted"},
            "decompose": last_decompose}


def _repair_prompt(verdict: dict) -> str:
    bits = [v["detail"] for v in verdict.get("violations", [])]
    if verdict.get("uncited_numbers"):
        bits.append("These figures appear in your answer but no tool produced them: "
                    f"{verdict['uncited_numbers']}. Use only numbers from tool results.")
    return ("Your answer was rejected by the faithfulness gate: " + " ".join(bits)
            + " Rewrite it using only the tool results, without new tool calls.")


@tracing.trace("faithfulness_gate", span_type="CHAIN")
def _gate(answer: str, decompose: dict | None,
          facts: list[float] | None = None) -> tuple[str, dict]:
    """Faithfulness gate (core.faithfulness). Narration may only name drivers the
    decomposition supports, in the direction it computed, using tool-sourced numbers.
    A failing answer is REPLACED by the computed attribution — a wrong explanation is
    worse than a terse one."""
    faith = FA.check_faithfulness(answer, decompose)
    nums = FA.check_numbers(answer, facts or []) if facts else {"ok": True, "uncited": []}
    verdict = {"ok": faith["ok"] and nums["ok"], "violations": faith["violations"],
               "uncited_numbers": nums.get("uncited", []), "supported": faith["supported"]}
    if verdict["ok"]:
        return answer, verdict
    if not decompose or not decompose.get("ok"):
        return answer, verdict                      # nothing better to fall back to
    lines = ["⚠️ The narration was rejected by the faithfulness gate.", "",
             "Computed attribution:"]
    for d in decompose["drivers"]:
        lines.append(f"- {d['label']}: {d['contribution']:+.4f} VRR "
                     f"({d['share']*100:.1f}% of the move)")
    return "\n".join(lines), verdict


def ask(question: str, pattern: str | None = None, date: str | None = None) -> dict:
    """Entry point the app uses — routes through `agent.chat` (deterministic first,
    LLM tool loop when a local model is up)."""
    from . import chat
    return chat.respond(question, pattern=pattern, date=date)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "List the patterns and their latest VRR."
    if llm.available():
        out = run(q)
        print(out["text"])
        print(f"\n[tools: {', '.join(t['tool'] for t in out['trace']) or 'none'} | "
              f"gate: {'passed' if out['gate']['ok'] else out['gate']}]")
    else:                       # no Ollama → deterministic path, still fully answerable
        print(ask(q)["text"])
