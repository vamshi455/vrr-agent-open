"""Request bodies for the write endpoints.

Only the WRITES are typed. Read endpoints return the tool payloads verbatim — the whole
value of `agent/tools.py` is that its output already carries provenance (`sources`,
`run_id`, `pvt_methods`, `formulas`), and re-declaring those shapes here would create a
second definition of truth that silently drifts from the first.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    pattern: str | None = None          # sidebar context: pattern the analyst is looking at
    date: str | None = None             # sidebar context: period under review, YYYY-MM-DD
    agentic: bool = False               # let the model drive the tool loop itself
    asked_by: str = "anonymous"
    persist: bool = True                # write the turn to vrr_agent.chat_history


class SubmitRequest(BaseModel):
    date: str
    submitted_by: str


class StageRequest(BaseModel):
    """Advancing or rejecting a queued action.

    `role` is what the caller claims to be acting as, and the server checks it against
    the stage — the client is never trusted to decide whether a transition is allowed.
    """
    role: str
    user: str
