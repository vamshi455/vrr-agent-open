"""MLflow tracing, made optional and quiet.

The agent must run on a box with no MLflow installed and no tracking server up — so
tracing is probed once at import and degrades to a no-op decorator. When a server IS
reachable, every span lands in one experiment and the whole question becomes an
inspectable tree:

    chat.respond                    (AGENT)
      └ analyst.analyze             (CHAIN)      or  graph.run (agentic)
          ├ tool: VRR_AUDIT         (TOOL)           ├ llm.chat        (LLM)
          ├ tool: VRR_DECOMPOSE     (TOOL)           ├ tool: …         (TOOL)
          └ tool: RECOMMEND_CHANGE  (TOOL)           └ gate            (CHAIN)

Enable/point it with env vars — nothing here has side effects beyond the probe:
    MLFLOW_TRACKING_URI=http://localhost:5001   (5000 is taken by AirPlay on macOS)
    VRR_TRACING=0                               to force it off
"""
from __future__ import annotations

import os
from functools import wraps

from ..config import load_config

CFG = load_config()
EXPERIMENT = os.environ.get("VRR_MLFLOW_EXPERIMENT", "vrr-agent-open")

_mlflow = None
_enabled = False


def _probe() -> bool:
    """Is a tracking server actually listening? Keeps a dead URI from costing latency."""
    if os.environ.get("VRR_TRACING", "1") == "0":
        return False
    try:
        import httpx

        return httpx.get(f"{CFG.mlflow_uri}/health", timeout=0.7).status_code == 200
    except Exception:
        return False


try:                                                    # pragma: no cover - env dependent
    if _probe():
        import mlflow as _mlflow                        # noqa: F811

        _mlflow.set_tracking_uri(CFG.mlflow_uri)
        _mlflow.set_experiment(EXPERIMENT)
        _enabled = True
except Exception:
    _mlflow, _enabled = None, False


def enabled() -> bool:
    return _enabled


def status() -> str:
    return (f"🟢 tracing → {CFG.mlflow_uri} ({EXPERIMENT})" if _enabled
            else "⚪ tracing off (no MLflow server at " + CFG.mlflow_uri + ")")


def trace(name: str | None = None, span_type: str = "CHAIN"):
    """Decorator: record the call as a span when tracing is live, else pass through."""
    def deco(fn):
        if not _enabled:
            return fn

        @wraps(fn)
        def wrapper(*a, **kw):
            with _mlflow.start_span(name=name or fn.__qualname__,
                                    span_type=span_type) as span:
                try:
                    span.set_inputs({"args": [str(x)[:500] for x in a],
                                     "kwargs": {k: str(v)[:500] for k, v in kw.items()}})
                except Exception:
                    pass
                out = fn(*a, **kw)
                try:
                    span.set_outputs({"result": str(out)[:2000]})
                except Exception:
                    pass
                return out
        return wrapper
    return deco
