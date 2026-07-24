"""Deterministic VRR tools over PostgreSQL (data + compute) — the ONLY source of
numbers the agent may narrate. Same trust model as the Databricks version: the LLM
picks tools + narrates; every figure comes from here with provenance.

Postgres replaces the SQL warehouse; psycopg replaces the Spark/warehouse layer.
The tool CONTRACTS (names, args, returns) match the Databricks tools so the agent
graph and the faithfulness gate are unchanged.
"""
from __future__ import annotations

from typing import Any

import psycopg

from ..config import load_config

CFG = load_config()


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with psycopg.connect(CFG.pg_dsn, row_factory=psycopg.rows.dict_row) as c:
        with c.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []


# ---- the 8 deterministic tools (subset shown; contracts match Databricks) ----

def list_patterns() -> list[dict]:
    return _rows("SELECT pattern_id, max(pattern_name) pattern_name, count(*) n, "
                 "max(vrr_date) last_date FROM vrr_curated.pattern_vrr_monthly "
                 "GROUP BY pattern_id ORDER BY pattern_id")


def vrr_get(pattern: str, date: str) -> dict:
    r = _rows("SELECT * FROM vrr_curated.pattern_vrr_monthly "
              "WHERE pattern_id=%(p)s AND vrr_date=%(d)s", {"p": pattern, "d": date})
    if not r:
        return {"found": False, "pattern": pattern, "date": date}
    row = r[0]
    row["found"] = True
    row["provenance"] = {"table": "vrr_curated.pattern_vrr_monthly",
                         "keys": {"pattern_id": pattern, "vrr_date": date}}
    return row


def vrr_decompose(pattern: str, date_a: str, date_b: str) -> dict:
    """ΔVRR attribution a→b via the same LMDI log-mean math as the Databricks tool
    (see docs/design.md). Reads contrib term totals from Postgres."""
    # (implementation ports agent/tools.vrr_decompose using _rows over
    #  vrr_curated.completion_contrib — omitted here for brevity in the scaffold)
    raise NotImplementedError("port vrr_decompose from the Databricks tools.py")


TOOL_SPECS: list[dict[str, Any]] = [
    {"type": "function", "function": {"name": "LIST_PATTERNS",
        "description": "List patterns and their latest VRR.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "VRR_GET",
        "description": "Get a pattern's VRR + provenance on a date.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "date": {"type": "string"}}, "required": ["pattern", "date"]}}},
    {"type": "function", "function": {"name": "VRR_DECOMPOSE",
        "description": "Attribute a VRR change between two dates to its drivers (LMDI).",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "date_a": {"type": "string"}, "date_b": {"type": "string"}},
            "required": ["pattern", "date_a", "date_b"]}}},
]

DISPATCH = {"LIST_PATTERNS": lambda a: list_patterns(),
            "VRR_GET": lambda a: vrr_get(a["pattern"], a["date"]),
            "VRR_DECOMPOSE": lambda a: vrr_decompose(a["pattern"], a["date_a"], a["date_b"])}


def call_tool(name: str, args: dict) -> dict:
    fn = DISPATCH.get(name)
    return fn(args) if fn else {"error": f"unknown tool {name}"}
