"""LangGraph agent — the OSS equivalent of the Databricks ChatAgent tool loop.

Graph shape (mirrors serving/model.py):

    plan ─▶ tools ─▶ plan ─▶ … ─▶ gate ─▶ END
      │ (LLM picks tool + args)      │ (faithfulness check of drivers vs narration)
      └──────────── loop ───────────┘

Everything deterministic lives in tools.py (over Postgres) + core/ (physics/decompose).
The LLM (Ollama by default) only plans tool calls and narrates. MLflow traces every
node so the loop is auditable — the OSS stand-in for Databricks trace spans.

This is a runnable skeleton: wire your LLM client in `_llm` and the decompose
faithfulness check in `gate`. Run: `python -m vrr_agent_open.agent.graph "<question>"`.
"""
from __future__ import annotations

import json
import sys
from typing import Annotated, TypedDict

import mlflow

from ..config import load_config
from ..core import faithfulness as FA
from . import chat
from . import tools as T

CFG = load_config()
mlflow.set_tracking_uri(CFG.mlflow_uri)

SYSTEM = ("You are the VRR analyst. You NEVER do arithmetic — call tools for every "
          "number and only name drivers the VRR_DECOMPOSE result supports. Cite "
          "provenance (table + keys) for figures.")


class State(TypedDict):
    messages: Annotated[list, lambda a, b: a + b]
    last_decompose: dict | None


def _llm(messages: list[dict], with_tools: bool = True) -> dict:
    """Call the local LLM (Ollama / any OpenAI-compatible endpoint). Returns the
    assistant message dict {content, tool_calls?}. Wire your client here."""
    import httpx
    payload = {"model": CFG.llm_model, "messages": messages, "stream": False}
    if with_tools:
        payload["tools"] = T.TOOL_SPECS
    r = httpx.post(f"{CFG.llm_base_url}/api/chat", json=payload, timeout=120)
    return r.json().get("message", {})


@mlflow.trace(span_type="AGENT")
def run(question: str, max_steps: int = 6) -> str:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": question}]
    last_decompose = None
    for _ in range(max_steps):
        msg = _llm(messages)
        calls = msg.get("tool_calls") or []
        if not calls:                                   # no tool → gate + return
            return _gate(msg.get("content", ""), last_decompose, messages)
        messages.append(msg)
        for tc in calls:
            fn = tc["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            result = T.call_tool(fn["name"], args)      # deterministic, over Postgres
            if fn["name"] == "VRR_DECOMPOSE" and result.get("ok"):
                last_decompose = result
            messages.append({"role": "tool", "name": fn["name"],
                             "content": json.dumps(result, default=str)})
    return "Could not complete within the tool budget — narrow the question."


@mlflow.trace(span_type="CHAIN")
def _gate(answer: str, decompose: dict | None, messages: list) -> str:
    """Faithfulness gate (core.faithfulness): narration may only name drivers the
    decomposition supports, in the direction it computed. A failing answer is
    REPLACED by the deterministic driver ranking — a wrong explanation is worse than
    a terse one."""
    check = FA.check_faithfulness(answer, decompose)
    if check["ok"]:
        return answer
    lines = ["⚠️ The narration was rejected by the faithfulness gate "
             f"({'; '.join(v['detail'] for v in check['violations'])})",
             "", "Computed attribution:"]
    for d in decompose["drivers"]:
        lines.append(f"- {d['label']}: {d['contribution']:+.4f} VRR "
                     f"({d['share']*100:.1f}% of the move)")
    return "\n".join(lines)


def ask(question: str, pattern: str | None = None, date: str | None = None) -> dict:
    """Entry point used by the Streamlit chat: deterministic tools first, LLM optional.

    ``run()`` above is the LLM-driven tool loop (needs Ollama with tool-calling). This
    is the loop the app uses because it degrades to a fully deterministic answer when
    no local model is installed. Same tools, same gate.
    """
    return chat.respond(question, pattern=pattern, date=date)


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "List the patterns and their latest VRR."
    if chat.llm_available():
        print(run(q))
    else:                       # no Ollama → deterministic path, still fully answerable
        print(ask(q)["text"])
