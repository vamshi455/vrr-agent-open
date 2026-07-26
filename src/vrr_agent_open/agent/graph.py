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
from ..prompts import DOMAIN  # noqa: F401  (re-exported: callers import it from here)

CFG = load_config()


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
