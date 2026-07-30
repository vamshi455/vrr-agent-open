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
    persist: bool = True                # write the turn to vrr_agent.chat_history
    # NOTE: no `asked_by` — the asker is the authenticated user. A client-supplied
    # identity in a shared transcript is a signature anyone can forge.


class SubmitRequest(BaseModel):
    date: str
    # no `submitted_by`: it is the token's subject.


# StageRequest is gone on purpose. Advancing or rejecting takes NO body at all: the
# actor is the token's subject and the role is its signed claim, so there was nothing
# left for the client to send — and anything it could send would be a claim about
# itself, which is exactly what this refactor removed.
