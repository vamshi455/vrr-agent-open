"""The judges were dead for a reason worth a regression test.

`.env` shipped `OPENAI_API_KEY=` for the optional hosted-narrator path. `load_dotenv`
puts that empty string into the environment, so the key is *present but falsy* — and
`os.environ.setdefault` only fills a key that is ABSENT. MLflow checks truthiness, so
every judge raised "OPENAI_API_KEY environment variable must be set" before it reached a
model, and `make eval` reported near-zero means that looked like bad judgement rather
than no judgement at all.
"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture
def reimport(monkeypatch):
    def _go(value):
        if value is None:
            monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        else:
            monkeypatch.setenv("OPENAI_API_KEY", value)
        import vrr_agent_open.evaluation.custom_judges as cj
        return importlib.reload(cj)
    return _go


def test_an_empty_key_from_dotenv_is_replaced(reimport):
    """The actual bug. `setdefault` left "" in place and MLflow refused to start."""
    reimport("")
    assert os.environ["OPENAI_API_KEY"] == "ollama-local"


def test_a_missing_key_is_filled(reimport):
    reimport(None)
    assert os.environ["OPENAI_API_KEY"] == "ollama-local"


def test_a_real_key_is_never_clobbered(reimport):
    """Someone judging with a hosted model must keep their own credential."""
    reimport("sk-a-real-key")
    assert os.environ["OPENAI_API_KEY"] == "sk-a-real-key"


def test_the_base_url_is_a_full_endpoint_not_an_api_root(reimport):
    """MLflow POSTs to this URL verbatim instead of appending /chat/completions, so a
    bare `…/v1` 404s silently and the judge dies with no visible error."""
    cj = reimport("")
    assert cj.JUDGE_BASE_URL.endswith("/chat/completions")
