"""Streamlit workbench smoke tests — the chat drawer and its durable history.

These need a live Postgres (they run the real app against real data), so they skip
unless `VRR_PG_DSN` reaches a database with the VRR schemas:

    PYTHONPATH=src VRR_PG_DSN=postgresql://vrr:vrr@localhost:55432/vrr pytest -q

What they guard: the column split that docks the drawer must not disturb the tabs, the
transcript must come from Postgres rather than session state (so a refresh keeps it),
and `chat.respond()` must stay free of history writes — otherwise `make traces` would
pour ten synthetic evaluation questions into the shared transcript.
"""
import pytest

APP = "src/vrr_agent_open/app/streamlit_app.py"
TEST_PATTERN = "APPTEST-PATTERN"

pytest.importorskip("streamlit.testing.v1")


def _db_or_skip():
    try:
        from vrr_agent_open.agent import history as H
        H.ensure_table()
        return H
    except Exception as exc:                                   # no server, no schema
        pytest.skip(f"no VRR Postgres reachable: {exc}")


@pytest.fixture
def hist():
    H = _db_or_skip()
    yield H
    with H._connect() as c, c.cursor() as cur:                 # never leave test rows
        cur.execute("DELETE FROM vrr_agent.chat_history WHERE id_pattern=%(p)s",
                    {"p": TEST_PATTERN})
        c.commit()


@pytest.fixture
def app():
    from streamlit.testing.v1 import AppTest
    _db_or_skip()
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    if at.exception:
        pytest.fail(f"app raised: {at.exception[0].value}")
    return at


def test_tabs_survive_the_drawer_split(app):
    assert [t.label for t in app.tabs] == [
        "🗺️ Portfolio", "📈 Report", "🔎 Lineage & audit", "✅ Approval queue"]


def test_drawer_is_open_with_one_chat_input(app):
    assert app.session_state["drawer_open"] is True
    assert len(app.chat_input) == 1


def test_toggle_collapses_the_drawer_but_keeps_the_tabs(app):
    toggle = [b for b in app.button if b.key == "drawer_toggle"][0]
    toggle.click().run()
    assert not app.exception
    assert app.session_state["drawer_open"] is False
    assert len(app.chat_input) == 0                # drawer body gone
    assert len(app.tabs) == 4                      # tab content untouched


def test_ensure_table_is_idempotent(hist):
    hist.ensure_table()
    hist.ensure_table()
    with hist._connect() as c, c.cursor() as cur:
        cur.execute("SELECT to_regclass('vrr_agent.chat_history') IS NOT NULL")
        assert cur.fetchone()[0] is True


def test_history_is_scoped_to_one_pattern(hist):
    hist.log_turn(pattern_id=TEST_PATTERN, pattern_name="APPTEST", date=None,
                  question="scoped?", result={"text": "yes"}, asked_by="pytest")
    assert [t["question"] for t in hist.recent(TEST_PATTERN)] == ["scoped?"]
    assert not [t for t in hist.recent("SOME-OTHER-PATTERN")
                if t["question"] == "scoped?"]


def test_respond_does_not_write_history(hist):
    """The eval harness calls respond() ten times per `make traces`."""
    from vrr_agent_open.agent import chat as CH
    from vrr_agent_open.agent import tools as T

    pid = T.list_patterns()[0]["pattern_id"]
    with hist._connect() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM vrr_agent.chat_history")
        before = cur.fetchone()[0]
    CH.respond("How is VRR calculated?", pattern=pid, use_llm=False)
    with hist._connect() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM vrr_agent.chat_history")
        assert cur.fetchone()[0] == before
