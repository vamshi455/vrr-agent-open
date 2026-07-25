"""Local LLM client (Ollama by default) — the only place the agent talks to a model.

Kept deliberately thin: one ``chat()`` that speaks the OpenAI-style
``messages``/``tools`` shape Ollama implements at ``/api/chat``. Point
``VRR_LLM_BASE_URL`` at any other OpenAI-compatible endpoint and nothing else changes.

The model is never trusted with arithmetic — see ``agent/graph.py`` for the tool loop
and ``core/faithfulness.py`` for the gate that checks what comes back.
"""
from __future__ import annotations

import httpx

from ..config import load_config

CFG = load_config()


def available(timeout: float = 1.5) -> bool:
    """Is a local model server reachable right now?"""
    try:
        return httpx.get(f"{CFG.llm_base_url}/api/tags", timeout=timeout).status_code == 200
    except Exception:
        return False


def models() -> list[str]:
    try:
        r = httpx.get(f"{CFG.llm_base_url}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def pick_model(preferred: str | None = None) -> str | None:
    """Resolve the configured model against what is actually pulled locally.

    Falls back to any installed chat model (skipping embedding-only models) so a fresh
    machine with a different pull still works instead of erroring on model-not-found.
    """
    have = models()
    want = preferred or CFG.llm_model
    if not have:
        return None
    for name in have:                                  # exact, then tag-insensitive
        if name == want or name.split(":")[0] == want.split(":")[0]:
            return name
    chat_models = [m for m in have if "embed" not in m]
    return chat_models[0] if chat_models else None


def chat(messages: list[dict], tools: list[dict] | None = None,
         model: str | None = None, temperature: float = 0.0,
         timeout: float = 180) -> dict:
    """One turn. Returns the assistant message dict ``{content, tool_calls?}``.

    ``temperature=0`` by default: this agent narrates computed results, so sampling
    variety is a liability, not a feature.
    """
    payload: dict = {"model": model or pick_model() or CFG.llm_model,
                     "messages": messages, "stream": False,
                     "options": {"temperature": temperature}}
    if tools:
        payload["tools"] = tools
    r = httpx.post(f"{CFG.llm_base_url}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json().get("message", {}) or {}
