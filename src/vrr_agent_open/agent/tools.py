"""Deterministic VRR tools over PostgreSQL (data + compute) — the ONLY source of
numbers the agent may narrate. Same trust model as the Databricks version: the LLM
picks tools + narrates; every figure comes from here with provenance.

Postgres replaces the SQL warehouse; psycopg replaces the Spark/warehouse layer.
The tool CONTRACTS (names, args, returns) match the Databricks tools so the agent
graph and the faithfulness gate are unchanged.

The toolbelt, in the order an analyst usually needs it:
  LIST_PATTERNS      what patterns exist, latest VRR
  VRR_TREND          the series behind the chart (date-filtered)
  VRR_GET            one period + provenance
  VRR_DECOMPOSE      ΔVRR attribution a→b (core.decompose, exact + additive)
  VRR_LINEAGE        how THIS number was built: monthly ← completions ← raw + PVT
  VRR_AUDIT          recompute from raw with core.physics and diff vs the stored value
  PATTERN_CONTEXT    target, learned band/ρ, safety limits, prior adjustments
  DETECT_ANOMALIES   core.anomaly over the pattern's history
  RECOMMEND_CHANGE   core.recommend — bounded, ρ-calibrated valve change
  SUBMIT_FOR_APPROVAL write the draft into the action queue (stage='draft')
  SEARCH_KNOWLEDGE   pgvector search over ingested reservoir docs (needs embeddings)
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Optional

import psycopg

from ..config import load_config
from ..core import anomaly as AN
from ..core import decompose as DC
from ..core import physics
from ..core import recommend as RE

CFG = load_config()

TERM_COLUMNS = ("oil_res", "water_res", "free_gas_res", "water_inj_res", "gas_inj_res")


def _rows(sql: str, params: dict | None = None) -> list[dict]:
    with psycopg.connect(CFG.pg_dsn, row_factory=psycopg.rows.dict_row) as c:
        with c.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []


def _execute(sql: str, params: dict) -> int:
    with psycopg.connect(CFG.pg_dsn) as c, c.cursor() as cur:
        cur.execute(sql, params)
        n = cur.rowcount
        c.commit()
    return n


def _resolve(pattern: str) -> Optional[dict]:
    """Accept either a pattern_id ('PAT-001') or a name ('UNITY'), case-insensitive."""
    r = _rows("SELECT DISTINCT pattern_id, pattern_name FROM vrr_curated.pattern_vrr_monthly "
              "WHERE upper(pattern_id)=upper(%(p)s) OR upper(pattern_name)=upper(%(p)s)",
              {"p": pattern})
    return r[0] if r else None


# ---- the deterministic tools ------------------------------------------------

def list_patterns() -> list[dict]:
    return _rows("SELECT pattern_id, max(pattern_name) pattern_name, count(*) n, "
                 "max(vrr_date) last_date FROM vrr_curated.pattern_vrr_monthly "
                 "GROUP BY pattern_id ORDER BY pattern_id")


def vrr_trend(pattern: str, date_from: str | None = None,
              date_to: str | None = None) -> dict:
    """The pattern's VRR series (the data behind the chart), optionally date-filtered."""
    p = _resolve(pattern)
    if not p:
        return {"found": False, "pattern": pattern}
    rows = _rows(
        "SELECT pattern_id, pattern_name, vrr_date, vrr, prod_res_bbl, inj_res_bbl,"
        " n_completions, any_extrapolated, run_id FROM vrr_curated.pattern_vrr_monthly"
        " WHERE pattern_id=%(p)s AND (%(a)s::date IS NULL OR vrr_date >= %(a)s::date)"
        " AND (%(b)s::date IS NULL OR vrr_date <= %(b)s::date) ORDER BY vrr_date",
        {"p": p["pattern_id"], "a": date_from, "b": date_to})
    return {"found": bool(rows), "pattern_id": p["pattern_id"],
            "pattern_name": p["pattern_name"], "rows": rows,
            "provenance": {"table": "vrr_curated.pattern_vrr_monthly",
                           "filter": {"pattern_id": p["pattern_id"],
                                      "from": date_from, "to": date_to}}}


def vrr_get(pattern: str, date: str) -> dict:
    p = _resolve(pattern)
    pid = p["pattern_id"] if p else pattern
    r = _rows("SELECT * FROM vrr_curated.pattern_vrr_monthly "
              "WHERE pattern_id=%(p)s AND vrr_date=%(d)s", {"p": pid, "d": date})
    if not r:
        return {"found": False, "pattern": pattern, "date": date}
    row = r[0]
    row["found"] = True
    row["provenance"] = {"table": "vrr_curated.pattern_vrr_monthly",
                         "keys": {"pattern_id": pid, "vrr_date": date}}
    return row


def _term_totals(pattern_id: str, date: str) -> dict:
    r = _rows(
        "SELECT sum(oil_res) oil_res, sum(water_res) water_res,"
        " sum(coalesce(free_gas_res,0)) free_gas_res, sum(water_inj_res) water_inj_res,"
        " sum(gas_inj_res) gas_inj_res FROM vrr_curated.completion_contrib"
        " WHERE pattern_id=%(p)s AND date_trunc('month', vrr_date)=%(d)s::date",
        {"p": pattern_id, "d": date})
    return {k: (v or 0.0) for k, v in (r[0] if r else {}).items()}


def vrr_decompose(pattern: str, date_a: str, date_b: str) -> dict:
    """ΔVRR attribution a→b via the exact log-mean (LMDI) math in ``core.decompose``.

    Term totals come from ``vrr_curated.completion_contrib`` — the lineage layer — so
    the attribution is traceable to the same rows that built the monthly VRR.
    """
    p = _resolve(pattern)
    if not p:
        return {"ok": False, "reason": f"unknown pattern '{pattern}'"}
    a, b = _term_totals(p["pattern_id"], date_a), _term_totals(p["pattern_id"], date_b)
    if not a or not b:
        return {"ok": False, "reason": "no contribution rows for one of the periods"}
    result = DC.decompose_vrr(a, b)
    result.update({"pattern_id": p["pattern_id"], "pattern_name": p["pattern_name"],
                   "date_a": date_a, "date_b": date_b,
                   "provenance": {"table": "vrr_curated.completion_contrib",
                                  "grain": "sum of *_res terms per month"}})
    return result


def vrr_lineage(pattern: str, date: str) -> dict:
    """Full derivation of ONE monthly VRR: aggregate ← completions ← raw + PVT method.

    This is the lineage answer an auditor asks for — every root input, the PVT lookup
    method that produced the FVFs, the per-completion reservoir terms, and the formula
    each was computed with.
    """
    p = _resolve(pattern)
    if not p:
        return {"found": False, "pattern": pattern}
    pid = p["pattern_id"]
    monthly = vrr_get(pid, date)
    completions = _rows(
        "SELECT completion_id, count(*) n_days, min(pressure_psi) min_pressure,"
        " max(pressure_psi) max_pressure, string_agg(DISTINCT pvt_method, ',') pvt_methods,"
        " avg(factor) factor, sum(oil) oil, sum(water) water, sum(gas) gas,"
        " sum(water_inj) water_inj, sum(gas_inj) gas_inj, sum(oil_res) oil_res,"
        " sum(water_res) water_res, sum(coalesce(free_gas_res,0)) free_gas_res,"
        " sum(water_inj_res) water_inj_res, sum(gas_inj_res) gas_inj_res"
        " FROM vrr_curated.completion_contrib WHERE pattern_id=%(p)s"
        " AND date_trunc('month', vrr_date)=%(d)s::date GROUP BY completion_id"
        " ORDER BY completion_id", {"p": pid, "d": date})
    totals = _term_totals(pid, date)
    prod = sum(totals.get(t, 0.0) for t in DC.PROD_TERMS)
    inj = sum(totals.get(t, 0.0) for t in DC.INJ_TERMS)
    return {
        "found": bool(completions), "pattern_id": pid,
        "pattern_name": p["pattern_name"], "vrr_date": date,
        "monthly": monthly, "completions": completions, "term_totals": totals,
        "recomputed_from_terms": {"prod_res_bbl": prod, "inj_res_bbl": inj,
                                  "vrr": physics.vrr(inj, prod)},
        "formulas": {
            "oil_res": "FACTOR · OIL · Bo",
            "water_res": "FACTOR · WATER · Bw",
            "free_gas_res": "FACTOR · (GAS·1000 − Rs·OIL) · Bg   [producers, OIL>0]",
            "water_inj_res": "FACTOR · WATER_INJ · Bw_inj",
            "gas_inj_res": "FACTOR · GAS_INJ·1000 · Bg_inj",
            "vrr": "Σ(water_inj_res + gas_inj_res) / Σ(oil_res + water_res + free_gas_res)",
        },
        "sources": {
            "raw_volumes": "vrr_raw.production_volumes_daily",
            "pressure": "vrr_raw.pattern_pressure (latest reading ≤ production date)",
            "pvt": "vrr_raw.completion_pvt → core.physics.pvt_lookup (method per row)",
            "lineage_layer": "vrr_curated.completion_contrib",
            "aggregate": "vrr_curated.pattern_vrr_monthly",
            "code": "vrr_agent_open.core.physics.completion_contribution",
        },
    }


def vrr_audit(pattern: str, date: str, tolerance: float = 1e-6) -> dict:
    """Independently RECOMPUTE the month's VRR from raw tables and diff it vs stored.

    Answers "is the number on the screen actually right?" without trusting the curated
    layer: re-reads raw volumes/pressure/PVT, re-runs ``core.physics`` here, and
    compares. A mismatch means the curated build is stale or the raw data changed.
    """
    p = _resolve(pattern)
    if not p:
        return {"ok": False, "reason": f"unknown pattern '{pattern}'"}
    pid = p["pattern_id"]

    pvt_points: dict[str, list] = {}
    for r in _rows("SELECT * FROM vrr_raw.completion_pvt"):
        pvt_points.setdefault(r["completion_id"], []).append(physics.PVTPoint(
            pressure_psi=r["pressure_psi"], bo=r["bo"], bw=r["bw"], bg=r["bg"],
            rs=r["rs"], rv=r["rv"], bw_inj=r["bw_inj"], bg_inj=r["bg_inj"]))

    raw = _rows(
        "SELECT v.*, (SELECT pressure_psi FROM vrr_raw.pattern_pressure pp"
        "   WHERE pp.pattern_id=v.pattern_id AND pp.vrr_date<=v.vrr_date"
        "   ORDER BY pp.vrr_date DESC LIMIT 1) pressure_psi"
        " FROM vrr_raw.production_volumes_daily v WHERE v.pattern_id=%(p)s"
        " AND date_trunc('month', v.vrr_date)=%(d)s::date", {"p": pid, "d": date})
    if not raw:
        return {"ok": False, "reason": f"no raw rows for {pid} in {date}"}

    prod = inj = 0.0
    methods: set[str] = set()
    for r in raw:
        pvt = physics.pvt_lookup(pvt_points.get(r["completion_id"], []), r["pressure_psi"])
        methods.add(pvt.method)
        t = physics.completion_contribution(
            factor=r["factor"], oil=r["oil"], water=r["water"], gas=r["gas"],
            water_inj=r["water_inj"], gas_inj=r["gas_inj"], pvt=pvt.props,
            is_producer=(r["amount_type"] == "Production"))
        prod += t.prod_res
        inj += t.inj_res

    recomputed = physics.vrr(inj, prod)
    stored = vrr_get(pid, date)
    stored_vrr = stored.get("vrr")
    diff = None if (recomputed is None or stored_vrr is None) else recomputed - stored_vrr
    return {
        "ok": True, "pattern_id": pid, "pattern_name": p["pattern_name"],
        "vrr_date": date, "n_raw_rows": len(raw),
        "recomputed": {"vrr": recomputed, "prod_res_bbl": prod, "inj_res_bbl": inj},
        "stored": {"vrr": stored_vrr, "prod_res_bbl": stored.get("prod_res_bbl"),
                   "inj_res_bbl": stored.get("inj_res_bbl"), "run_id": stored.get("run_id")},
        "difference": diff,
        "matches": diff is not None and abs(diff) <= tolerance,
        "pvt_methods": sorted(methods),
        "low_confidence_inputs": bool(methods & {physics.EXTRAP, physics.CLOSEST, physics.NONE}),
        "provenance": {"recomputed_from": ["vrr_raw.production_volumes_daily",
                                           "vrr_raw.pattern_pressure",
                                           "vrr_raw.completion_pvt"],
                       "code": "core.physics.pvt_lookup + completion_contribution"},
    }


def pattern_context(pattern: str) -> dict:
    """Target, learned band/ρ, safety limits and prior adjustments for one pattern."""
    p = _resolve(pattern)
    if not p:
        return {"found": False, "pattern": pattern}
    pid = p["pattern_id"]
    mem = _rows("SELECT * FROM vrr_agent.pattern_memory WHERE pattern_id=%(p)s", {"p": pid})
    tgt = _rows("SELECT target_vrr FROM vrr_raw.pattern_target WHERE pattern_id=%(p)s", {"p": pid})
    return {
        "found": True, "pattern_id": pid, "pattern_name": p["pattern_name"],
        "target_vrr": (tgt[0]["target_vrr"] if tgt else CFG.default_target_vrr),
        "memory": mem[0] if mem else {},
        "safety_limits": _rows("SELECT * FROM vrr_agent.safety_limits WHERE pattern_id=%(p)s",
                               {"p": pid}),
        "adjustment_history": _rows(
            "SELECT * FROM vrr_agent.adjustment_history WHERE pattern_id=%(p)s"
            " ORDER BY ts DESC LIMIT 20", {"p": pid}),
    }


def detect_anomalies(pattern: str) -> dict:
    """core.anomaly over the pattern's full monthly history (band from memory)."""
    ctx = pattern_context(pattern)
    if not ctx.get("found"):
        return {"ok": False, "reason": f"unknown pattern '{pattern}'"}
    hist = vrr_trend(ctx["pattern_id"])["rows"]
    mem = ctx.get("memory") or {}
    band = (mem.get("typical_low"), mem.get("typical_high"))
    found = AN.detect_anomalies(hist, target_vrr=ctx["target_vrr"],
                                band=band if all(band) else None)
    return {"ok": True, "pattern_id": ctx["pattern_id"],
            "pattern_name": ctx["pattern_name"], "target_vrr": ctx["target_vrr"],
            "band": band if all(band) else None,
            "anomalies": [a.__dict__ for a in found]}


def _injector_states(pattern_id: str, date: str) -> list[RE.InjectorState]:
    """Current injector state for the month, straight off the lineage layer.
    ``bw_inj`` is recovered as inj_res/(FACTOR·surface) — the same FVF the build used."""
    rows = _rows(
        "SELECT completion_id, avg(factor) factor, sum(water_inj) surface,"
        " sum(water_inj_res) inj_res FROM vrr_curated.completion_contrib"
        " WHERE pattern_id=%(p)s AND date_trunc('month', vrr_date)=%(d)s::date"
        " AND water_inj > 0 GROUP BY completion_id ORDER BY completion_id",
        {"p": pattern_id, "d": date})
    out = []
    for r in rows:
        denom = (r["factor"] or 0) * (r["surface"] or 0)
        out.append(RE.InjectorState(
            completion_id=r["completion_id"], factor=r["factor"],
            bw_inj=(r["inj_res"] / denom) if denom else 0.0,
            water_inj_surface=r["surface"], inj_res=r["inj_res"]))
    return out


def recommend_change(pattern: str, date: str | None = None) -> dict:
    """core.recommend — bounded, ρ-calibrated injection change for one period."""
    ctx = pattern_context(pattern)
    if not ctx.get("found"):
        return {"ok": False, "reason": f"unknown pattern '{pattern}'"}
    pid = ctx["pattern_id"]
    if not date:
        rows = vrr_trend(pid)["rows"]
        if not rows:
            return {"ok": False, "reason": "no VRR history"}
        date = str(rows[-1]["vrr_date"])
    period = vrr_get(pid, date)
    if not period.get("found"):
        return {"ok": False, "reason": f"no VRR row for {pid} on {date}"}

    mem = ctx.get("memory") or {}
    limits = [lim["max_inj_rate_change_pct"] for lim in ctx["safety_limits"]
              if lim["max_inj_rate_change_pct"] is not None]
    rec = RE.recommend_injection_change(
        prod_res=period["prod_res_bbl"], inj_res=period["inj_res_bbl"],
        target_vrr=ctx["target_vrr"], injectors=_injector_states(pid, date),
        response_factor=mem.get("response_factor") or 1.0,
        max_change_pct=min(limits) if limits else RE.DEFAULT_MAX_CHANGE_PCT)
    rec.update({"pattern_id": pid, "pattern_name": ctx["pattern_name"], "vrr_date": date,
                "response_factor_source": "vrr_agent.pattern_memory",
                "safety_limit_source": "vrr_agent.safety_limits",
                "any_extrapolated": period.get("any_extrapolated")})
    return rec


def find_precedent(pattern: str, driver: str | None = None,
                   direction: str | None = None) -> dict:
    ctx = pattern_context(pattern)
    if not ctx.get("found"):
        return {"found": False}
    prec = RE.find_precedent(ctx["adjustment_history"], driver=driver, direction=direction)
    return {"found": bool(prec), **(prec or {})}


def submit_for_approval(pattern: str, date: str, *, draft: dict,
                        submitted_by: str = "agent") -> dict:
    """Write the draft into ``vrr_agent.action_queue`` at stage='draft'.

    The agent's authority ENDS here (guardrail §6): a draft is advisory, and every
    forward step (analyst → RM → site → executed) is a human act in the approval UI.
    """
    action_id = f"ACT-{uuid.uuid4().hex[:10]}"
    _execute(
        "INSERT INTO vrr_agent.action_queue (action_id, pattern_id, pattern_name,"
        " vrr_date, anomaly_kind, severity, anomaly_detail, driver, action_type,"
        " recommendation, precedent, confidence, narrative, stage, stage_by, stage_ts)"
        " VALUES (%(id)s,%(pid)s,%(name)s,%(d)s,%(kind)s,%(sev)s,%(det)s,%(drv)s,"
        " %(at)s,%(rec)s,%(prec)s,%(conf)s,%(narr)s,'draft',%(by)s, now())",
        {"id": action_id, "pid": draft.get("pattern_id", pattern),
         "name": draft.get("pattern_name"), "d": draft.get("vrr_date", date),
         "kind": draft.get("anomaly_kind"), "sev": draft.get("severity"),
         "det": draft.get("anomaly_detail"), "drv": draft.get("driver"),
         "at": draft.get("action_type"),
         "rec": json.dumps(draft.get("recommendation"), default=str),
         "prec": json.dumps(draft.get("precedent"), default=str),
         "conf": draft.get("confidence"), "narr": draft.get("narrative"),
         "by": submitted_by})
    return {"ok": True, "action_id": action_id, "stage": "draft",
            "next_approver": "analyst",
            "note": "Draft queued — advisory only until analyst → RM → site sign-off."}


def search_knowledge(query: str, k: int = 3) -> dict:
    """pgvector search over ingested reservoir docs. Needs a local embedding model."""
    try:
        from ..pipeline.knowledge_ingest import search
        return {"ok": True, "hits": search(query, k)}
    except Exception as e:                     # no Ollama / no ingested docs
        return {"ok": False, "reason": f"knowledge search unavailable: {e}"}


# ---- LLM-facing specs + dispatch --------------------------------------------

def _spec(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


_PATTERN = {"pattern": {"type": "string", "description": "pattern id or name"}}
_DATE = {"date": {"type": "string", "description": "month start, YYYY-MM-DD"}}

TOOL_SPECS: list[dict[str, Any]] = [
    _spec("LIST_PATTERNS", "List patterns and their latest VRR.", {}, []),
    _spec("VRR_TREND", "VRR series for a pattern, optionally date-filtered.",
          {**_PATTERN, "date_from": {"type": "string"}, "date_to": {"type": "string"}},
          ["pattern"]),
    _spec("VRR_GET", "Get a pattern's VRR + provenance on a date.",
          {**_PATTERN, **_DATE}, ["pattern", "date"]),
    _spec("VRR_DECOMPOSE", "Attribute a VRR change between two dates to its drivers.",
          {**_PATTERN, "date_a": {"type": "string"}, "date_b": {"type": "string"}},
          ["pattern", "date_a", "date_b"]),
    _spec("VRR_LINEAGE", "Show how a monthly VRR was built: completions, raw inputs, PVT.",
          {**_PATTERN, **_DATE}, ["pattern", "date"]),
    _spec("VRR_AUDIT", "Recompute a month's VRR from raw tables and diff vs the stored value.",
          {**_PATTERN, **_DATE}, ["pattern", "date"]),
    _spec("PATTERN_CONTEXT", "Target, learned band/rho, safety limits, adjustment history.",
          _PATTERN, ["pattern"]),
    _spec("DETECT_ANOMALIES", "Run the deterministic anomaly rules over a pattern.",
          _PATTERN, ["pattern"]),
    _spec("RECOMMEND_CHANGE", "Compute a bounded injection change to steer VRR to target.",
          {**_PATTERN, **_DATE}, ["pattern"]),
    _spec("FIND_PRECEDENT", "Most recent executed adjustment matching a driver.",
          {**_PATTERN, "driver": {"type": "string"}}, ["pattern"]),
    _spec("SEARCH_KNOWLEDGE", "Search ingested reservoir documents.",
          {"query": {"type": "string"}}, ["query"]),
]

DISPATCH = {
    "LIST_PATTERNS": lambda a: {"patterns": list_patterns()},
    "VRR_TREND": lambda a: vrr_trend(a["pattern"], a.get("date_from"), a.get("date_to")),
    "VRR_GET": lambda a: vrr_get(a["pattern"], a["date"]),
    "VRR_DECOMPOSE": lambda a: vrr_decompose(a["pattern"], a["date_a"], a["date_b"]),
    "VRR_LINEAGE": lambda a: vrr_lineage(a["pattern"], a["date"]),
    "VRR_AUDIT": lambda a: vrr_audit(a["pattern"], a["date"]),
    "PATTERN_CONTEXT": lambda a: pattern_context(a["pattern"]),
    "DETECT_ANOMALIES": lambda a: detect_anomalies(a["pattern"]),
    "RECOMMEND_CHANGE": lambda a: recommend_change(a["pattern"], a.get("date")),
    "FIND_PRECEDENT": lambda a: find_precedent(a["pattern"], a.get("driver")),
    "SEARCH_KNOWLEDGE": lambda a: search_knowledge(a["query"]),
}


def call_tool(name: str, args: dict) -> dict:
    fn = DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(args)
    except Exception as e:                      # tool errors are data, not crashes
        return {"error": f"{name} failed: {e}"}
