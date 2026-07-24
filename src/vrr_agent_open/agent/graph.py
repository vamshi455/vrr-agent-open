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
    """Faithfulness gate: reject narration naming a driver the decomposition doesn't
    support (port core faithfulness check). Skeleton passes through when no decompose."""
    if not decompose:
        return answer
    # TODO: port check_faithfulness(answer, decompose) from the Databricks agent.py
    return answer


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "List the patterns and their latest VRR."
    print(run(q))
