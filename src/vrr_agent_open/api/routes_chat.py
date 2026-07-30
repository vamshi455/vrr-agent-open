"""The analyst chat — one gated answer per request, plus the shared transcript.

Plain JSON, deliberately, not token streaming: `core.faithfulness` can only verify a
FINISHED answer, so streaming tokens would mean streaming text the gate has not yet
approved and might replace. Progress-event streaming (which tool is running) is the
sane future addition; streaming the narration itself is not.

`chat.respond()` stays free of history writes — the transcript is written HERE, so that
`make traces` can run the same function over the evaluation set without pouring ten
synthetic questions into the shared drawer.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..agent import chat as CH
from ..agent import history as HIST
from ..agent import tools as T
from .schemas import ChatRequest

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat")
def ask(body: ChatRequest) -> dict:
    """Answer one question. `agentic=true` lets the model drive the tool loop itself."""
    try:
        result = CH.respond(body.question, pattern=body.pattern, date=body.date,
                            agentic=body.agentic)
    except Exception as exc:                    # a tool/DB failure is a 502, not a crash
        raise HTTPException(502, f"agent failed: {exc}") from exc

    saved = False
    if body.persist and body.pattern:
        try:
            HIST.ensure_table()
            ctx = T.pattern_context(body.pattern) or {}
            HIST.log_turn(pattern_id=body.pattern, pattern_name=ctx.get("pattern_name"),
                          date=body.date, question=body.question, result=result,
                          asked_by=body.asked_by, agentic=body.agentic)
            saved = True
        except Exception:
            saved = False                       # the answer still stands; only durability is lost
    return {**result, "persisted": saved}


@router.get("/chat/history")
def history(pattern: str = Query(...), limit: int = Query(50, le=200)) -> list[dict]:
    """This pattern's transcript, oldest first — shared across users, survives a refresh."""
    try:
        HIST.ensure_table()
        return HIST.recent(pattern, limit=limit)
    except Exception:
        return []                               # no table yet is empty, not an error
