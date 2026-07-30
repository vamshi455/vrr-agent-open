"""FastAPI layer — routing, contracts, and the guardrails that must be server-side.

Postgres and the model are both stubbed, so these run in the off-DB `pytest -q` tier.
What they pin is not "does the endpoint return 200" but the two properties the API
exists to hold:

  1. every read is a pass-through to `agent/tools.py` — this layer computes NOTHING,
     so a figure on screen and a figure in an answer cannot disagree;
  2. the approval chain enforces roles on the SERVER — the Streamlit version only hid
     the button, which is UX, not a control.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vrr_agent_open.api import main as API
from vrr_agent_open.api import routes_approvals as RA
from vrr_agent_open.api import routes_chat as RC
from vrr_agent_open.api import routes_patterns as RP

PATTERN = "E693E0D116730002"
DRAFT = {"action_id": "ACT-1", "stage": "draft", "id_pattern": PATTERN,
         "pattern_name": "UNITY", "vrr_date": "2026-04-01", "severity": "high",
         "action_type": "reduce_injection", "recommendation": {"injector_changes": []},
         "driver": "water_inj_res", "narrative": "…"}


@pytest.fixture
def client():
    return TestClient(API.app)


# ------------------------------------------------------------------- reads ----
def test_reads_are_pass_throughs_not_computations(client, monkeypatch):
    """The endpoint must hand back the tool payload verbatim — provenance keys and all.

    If this layer ever reshapes a tool result, the number on screen stops being the
    number the tool produced, and the whole grounding argument weakens.
    """
    payload = {"ok": True, "vrr": 1.249, "run_id": "6a0a1f62be0b",
               "provenance": {"recomputed_from": ["vrr_raw.production_volumes_daily"]}}
    monkeypatch.setattr(RP.T, "vrr_audit", lambda p, d: payload)

    got = client.get(f"/api/patterns/{PATTERN}/audit", params={"date": "2026-04-01"})

    assert got.status_code == 200
    assert got.json() == payload            # verbatim, including provenance


def test_unknown_pattern_is_404_not_an_empty_page(client, monkeypatch):
    monkeypatch.setattr(RP.T, "pattern_context", lambda p: {})
    assert client.get("/api/patterns/NOPE/context").status_code == 404


def test_decompose_takes_from_and_to_as_query_aliases(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(RP.T, "vrr_decompose",
                        lambda p, a, b: seen.update(a=a, b=b) or {"ok": True})

    client.get(f"/api/patterns/{PATTERN}/decompose",
               params={"from": "2026-03-01", "to": "2026-04-01"})

    assert seen == {"a": "2026-03-01", "b": "2026-04-01"}


def test_health_never_raises_when_everything_is_down(client, monkeypatch):
    """A workbench that will not load because MLflow is down is a worse failure than
    the one it is reporting."""
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(API.LLM, "available", boom)
    monkeypatch.setattr(API, "query", boom)

    body = client.get("/api/health").json()

    assert body["llm"]["available"] is False
    assert body["knowledge"] == {"docs": 0, "chunks": 0}


# ------------------------------------------------------- approval guardrails ----
def test_wrong_role_cannot_advance_even_by_posting_directly(client, monkeypatch):
    """The control the Streamlit UI never had: a draft advances on ANALYST sign-off, so
    an RM posting the transition must be refused by the server."""
    monkeypatch.setattr(RA, "query", lambda sql, p=None: [DRAFT])
    wrote = []
    monkeypatch.setattr(RA, "execute", lambda sql, p: wrote.append(sql))

    r = client.post("/api/queue/ACT-1/advance", json={"role": "rm", "user": "rm.demo"})

    assert r.status_code == 403
    assert "analyst" in r.json()["detail"]
    assert wrote == []                      # nothing was written


def test_right_role_advances_one_stage(client, monkeypatch):
    monkeypatch.setattr(RA, "query", lambda sql, p=None: [DRAFT])
    monkeypatch.setattr(RA, "execute", lambda sql, p: None)

    body = client.post("/api/queue/ACT-1/advance",
                       json={"role": "analyst", "user": "a.demo"}).json()

    assert (body["from"], body["to"]) == ("draft", "analyst")
    assert body["wrote_adjustment_history"] is False


def test_executing_writes_adjustment_history_before_moving_the_stage(client, monkeypatch):
    """The ρ learning loop reads adjustment_history — an executed item with no row there
    would silently never be learned from."""
    monkeypatch.setattr(RA, "query", lambda sql, p=None: [{**DRAFT, "stage": "site"}])
    statements = []
    monkeypatch.setattr(RA, "execute", lambda sql, p: statements.append(sql))
    monkeypatch.setattr(RA.AP, "build_adjustment_row", lambda *a, **k: {})

    body = client.post("/api/queue/ACT-1/advance",
                       json={"role": "site", "user": "s.demo"}).json()

    assert body["to"] == "executed"
    assert "INSERT INTO vrr_agent.adjustment_history" in statements[0]
    assert "UPDATE vrr_agent.action_queue" in statements[1]      # history first, then stage


def test_terminal_stage_cannot_be_advanced(client, monkeypatch):
    monkeypatch.setattr(RA, "query", lambda sql, p=None: [{**DRAFT, "stage": "executed"}])

    r = client.post("/api/queue/ACT-1/advance", json={"role": "site", "user": "s"})

    assert r.status_code == 409


def test_unknown_action_is_404(client, monkeypatch):
    monkeypatch.setattr(RA, "query", lambda sql, p=None: [])
    assert client.post("/api/queue/NOPE/advance",
                       json={"role": "analyst", "user": "a"}).status_code == 404


def test_submit_refuses_when_no_anomaly_fired(client, monkeypatch):
    """Nothing to approve is a 400, not an empty queue row."""
    monkeypatch.setattr(RP.AZ, "analyze", lambda p, d: {"ok": True, "draft": None})

    r = client.post(f"/api/patterns/{PATTERN}/submit",
                    json={"date": "2026-04-01", "submitted_by": "a.demo"})

    assert r.status_code == 400
    assert "nothing to draft" in r.json()["detail"]


# -------------------------------------------------------------------- chat ----
def test_chat_returns_the_gated_answer_with_its_meta(client, monkeypatch):
    answer = {"intent": "explain", "text": "VRR 1.249 …",
              "meta": {"llm": True, "gate": "passed", "model": "qwen2.5:7b"}}
    monkeypatch.setattr(RC.CH, "respond", lambda *a, **k: answer)

    body = client.post("/api/chat", json={"question": "why?", "pattern": PATTERN,
                                          "persist": False}).json()

    assert body["text"] == answer["text"]
    assert body["meta"]["gate"] == "passed"          # provenance reaches the browser
    assert body["persisted"] is False


def test_chat_answer_survives_a_history_write_failure(client, monkeypatch):
    """Durability is a nice-to-have; the answer is not. A dead chat_history table must
    not turn a good answer into a 500."""
    monkeypatch.setattr(RC.CH, "respond", lambda *a, **k: {"text": "ok", "meta": {}})
    monkeypatch.setattr(RC.HIST, "ensure_table", lambda: (_ for _ in ()).throw(
        RuntimeError("no table")))

    body = client.post("/api/chat", json={"question": "why?", "pattern": PATTERN}).json()

    assert body["text"] == "ok"
    assert body["persisted"] is False


def test_agent_failure_is_a_502_not_a_stack_trace(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(RC.CH, "respond", boom)

    r = client.post("/api/chat", json={"question": "why?", "persist": False})

    assert r.status_code == 502
    assert "postgres is down" in r.json()["detail"]


def test_empty_question_is_rejected_by_the_schema(client):
    assert client.post("/api/chat", json={"question": ""}).status_code == 422


def test_history_is_empty_not_an_error_before_the_table_exists(client, monkeypatch):
    monkeypatch.setattr(RC.HIST, "ensure_table", lambda: (_ for _ in ()).throw(
        RuntimeError("relation does not exist")))

    r = client.get("/api/chat/history", params={"pattern": PATTERN})

    assert r.status_code == 200
    assert r.json() == []


def test_stages_endpoint_publishes_the_state_machine(client):
    """The UI must not hard-code the chain — it reads it."""
    body = client.get("/api/stages").json()

    assert body["stages"][:4] == ["draft", "analyst", "rm", "site"]
    assert body["approver_for_stage"]["draft"] == "analyst"
