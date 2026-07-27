"""VRR review + approval workbench (OSS) — Streamlit over PostgreSQL.

One workflow: look at the trend → prove the number is right → ask the agent why →
send a bounded valve change up the approval chain.

  🗺️ Portfolio     every pattern's latest VRR vs target, ranked by drift
  📈 Report        per-pattern VRR chart (date-filtered) + target band + anomaly
                   markers, ΔVRR attribution, and the draft valve change for approval
  🔎 Lineage       how THIS number was built — raw tables → PVT method → per-completion
                   terms → monthly aggregate, plus an independent recompute (VRR_AUDIT)
  ✅ Approval      role-gated draft → analyst → RM → site → executed; executing writes
                   vrr_agent.adjustment_history, closing the ρ-learning loop.

💬 The analyst chat is NOT a tab — it is a collapsible drawer docked on the RIGHT, beside
whichever tab is open, so you can ask about the chart without leaving the chart. Its
transcript lives in `vrr_agent.chat_history` (agent/history.py), scoped to the selected
pattern and shared across users: it survives a refresh, and opening a pattern shows what
anyone already asked about it. Answers are computed by deterministic tools and only
*phrased* by the LLM, behind the faithfulness gate.

Every number rendered here comes from `agent.tools` / `core.*` — the app does no
arithmetic of its own. Run: `make app`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from vrr_agent_open.config import load_config
from vrr_agent_open.core import approval as AP
from vrr_agent_open.core import ids as IDS
from vrr_agent_open.agent import analyst as AZ
from vrr_agent_open.agent import chat as CH
from vrr_agent_open.agent import history as HIST
from vrr_agent_open.agent import llm as LLM
from vrr_agent_open.agent import tools as T
from vrr_agent_open.agent import tracing as TRACING

CFG = load_config()
st.set_page_config(page_title="VRR — Open", layout="wide", page_icon="🛢️")

# The repo's only injected CSS, and it is purely cosmetic: it keeps the chat drawer in
# view while the tab content scrolls. `st.container(key=…)` emits `.st-key-<key>`, so the
# selector is a documented contract rather than an internal class name. If a future
# Streamlit nests the drawer under an `overflow:hidden` block, sticky silently becomes
# static and the drawer still works — the transcript has its own bounded height anyway.
st.markdown("<style>.st-key-vrr_chat_drawer{position:sticky;top:3.5rem;}</style>",
            unsafe_allow_html=True)

# Which role is allowed to advance a draft that currently sits at each stage.
APPROVER_FOR_STAGE = {"draft": "analyst", "analyst": "rm", "rm": "site", "site": "site"}
ROLES = ["analyst", "rm", "site"]


def q(sql: str, params: dict | None = None) -> list[dict]:
    with psycopg.connect(CFG.pg_dsn, row_factory=psycopg.rows.dict_row) as c:
        with c.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []


def execute(sql: str, params: dict) -> None:
    with psycopg.connect(CFG.pg_dsn) as c, c.cursor() as cur:
        cur.execute(sql, params)
        c.commit()


@st.cache_data(ttl=60)
def patterns() -> list[dict]:
    return T.list_patterns()


@st.cache_data(ttl=60)
def trend(pattern_id: str) -> list[dict]:
    return T.vrr_trend(pattern_id)["rows"]


# ---- sidebar: the filters every tab reads -----------------------------------
pats = patterns()
if not pats:
    st.error("No VRR data yet — run `make seed`.")
    st.stop()

st.sidebar.title("🛢️ VRR — Open")
# IDs are opaque 16-char hex keys — show a short form, keep the full one in provenance
label = {f"{p['pattern_name']} ({IDS.short(p['pattern_id'])})": p["pattern_id"]
         for p in sorted(pats, key=lambda r: r["pattern_name"] or "")}
pid = label[st.sidebar.selectbox("Pattern", list(label))]
rows = trend(pid)
dates = [r["vrr_date"] for r in rows]
d_from, d_to = st.sidebar.select_slider(
    "Date range", options=dates, value=(dates[0], dates[-1]),
    format_func=lambda d: d.strftime("%b %Y"))
period = st.sidebar.selectbox("Period under review", [d for d in dates if d_from <= d <= d_to],
                              index=len([d for d in dates if d_from <= d <= d_to]) - 1,
                              format_func=lambda d: d.strftime("%b %Y"))
role = st.sidebar.selectbox("Acting as", ROLES)
user = st.sidebar.text_input("Your name", value=f"{role}.demo")

st.sidebar.divider()
llm_up = CH.llm_available()
model_name = LLM.pick_model() if llm_up else None
st.sidebar.caption(
    f"LLM narrator: {'🟢 ' + str(model_name) if llm_up else '⚪ not running'}  \n"
    + ("Phrasing is LLM-generated and gated; numbers stay tool-computed."
       if llm_up else "Answers are fully computed — no LLM needed."))
st.sidebar.caption(f"Postgres: `{CFG.pg_dsn.split('@')[-1]}`")
st.sidebar.caption(
    f"[MLflow traces]({CFG.mlflow_uri}) — every question is a span tree "
    "(tools · LLM · gate)" if TRACING.enabled() else
    f"⚪ MLflow tracing off (no server at {CFG.mlflow_uri})")
try:                        # knowledge index (pgvector) — empty until docs are ingested
    kb = q("SELECT count(DISTINCT doc_id) docs, count(*) chunks "
           "FROM vrr_agent.reservoir_knowledge")[0]
    st.sidebar.caption(f"Knowledge index: {kb['docs']} doc(s), {kb['chunks']} chunks"
                       + ("" if kb["chunks"] else " — run `make knowledge`"))
except Exception:
    pass

ctx = T.pattern_context(pid)
target = ctx["target_vrr"]
mem = ctx.get("memory") or {}
band = (mem.get("typical_low") or 0.9, mem.get("typical_high") or 1.1)
window = [r for r in rows if d_from <= r["vrr_date"] <= d_to]
df = pd.DataFrame(window)

# Page splits into tab content + the chat drawer. BOTH columns always exist and only the
# ratio changes: creating a single column when the drawer is shut would move the tab
# element in the delta tree, and Streamlit keys the selected tab by element path — every
# toggle would kick you back to Portfolio.
if "drawer_open" not in st.session_state:
    st.session_state.drawer_open = True
main, drawer = st.columns([2.5, 1] if st.session_state.drawer_open else [16, 1],
                          gap="medium")

# `main.tabs(...)` rather than `with main:` — the tab bodies below then need no
# re-indentation, so this restructure touches none of them.
tab_portfolio, tab_report, tab_lineage, tab_approve = main.tabs(
    ["🗺️ Portfolio", "📈 Report", "🔎 Lineage & audit", "✅ Approval queue"])

# ------------------------------------------------------------- Portfolio ----
with tab_portfolio:
    st.subheader("Portfolio — where to look first")
    st.caption("Every pattern's latest VRR against its target, ranked by absolute drift "
               "(VRR_OVERVIEW over vrr_curated.pattern_vrr). Orange = built on "
               "low-confidence PVT, so the number itself is suspect.")
    assets = sorted({p.get("asset") for p in pats if p.get("asset")}) if pats else []
    asset = st.selectbox("Asset", ["all assets", *assets])
    ov = T.vrr_overview(None if asset == "all assets" else asset)
    pf = pd.DataFrame(ov["patterns"])
    if pf.empty:
        st.info("No patterns for that asset.")
    else:
        m1, m2, m3 = st.columns(3)
        m1.metric("Patterns", ov["n_patterns"])
        m2.metric("Off target", len(ov["off_target"]))
        m3.metric("Suspect inputs", int(pf["any_extrapolated"].sum()))
        st.altair_chart(
            alt.Chart(pf.head(20)).mark_bar().encode(
                x=alt.X("drift:Q", title="|VRR − target|"),
                y=alt.Y("pattern_name:N", sort="-x", title=None),
                color=alt.condition(alt.datum.any_extrapolated,
                                    alt.value("#ff7f0e"), alt.value("#1f77b4")),
                tooltip=["pattern_name", "asset", alt.Tooltip("vrr:Q", format=".3f"),
                         alt.Tooltip("target_vrr:Q", format=".2f"), "verdict",
                         "any_extrapolated", "vrr_date:T"]).properties(height=420),
            width="stretch")
        show = pf[["pattern_name", "asset", "vrr_date", "vrr", "target_vrr", "verdict",
                   "n_completions", "any_extrapolated", "response_factor", "id_pattern"]]
        st.dataframe(show, width="stretch", hide_index=True)
        st.caption("Pick a pattern in the sidebar to open it in the other tabs.")

    audits = T.input_audit()
    if audits.get("n"):
        st.caption("Input-audit verdicts (latest audited period per pattern): "
                   + " · ".join(f"**{k}** {v}" for k, v in sorted(audits["by_verdict"].items()))
                   + " — only REAL_SIGNAL periods may carry a valve recommendation.")

    with st.expander("Ingestion data quality (DATA_QUALITY)"):
        dq = T.data_quality()
        if dq.get("ok"):
            st.success(f"All {len(dq['checks_run'])} checks clean — allocation sums ≤ 1, "
                       "no orphan volumes, every pattern has pressure, every allocated "
                       "completion has PVT.")
        else:
            st.warning(f"{dq['n_findings']} finding(s)")
            for name, rows_ in (dq.get("findings") or {}).items():
                st.markdown(f"**{name}** — {len(rows_)} row(s)")
                st.dataframe(pd.DataFrame(rows_), width="stretch", hide_index=True)

# ---------------------------------------------------------------- Report ----
with tab_report:
    sel = next(r for r in rows if r["vrr_date"] == period)
    prev = next((r for r in rows if r["vrr_date"] < period), None)
    prev = [r for r in rows if r["vrr_date"] < period]
    prev = prev[-1] if prev else None

    st.subheader(f"{ctx['pattern_name']} — VRR {sel['vrr']:.3f} on {period:%b %Y}")
    st.caption(f"id_pattern `{pid}`" + (f" · asset {ctx.get('asset')}" if ctx.get("asset") else ""))
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("VRR", f"{sel['vrr']:.3f}",
              f"{sel['vrr'] - prev['vrr']:+.3f} MoM" if prev else None)
    k2.metric("Target", f"{target:.2f}", f"band {band[0]:.2f}–{band[1]:.2f}",
              delta_color="off")
    k3.metric("Injection (res bbl)", f"{sel['inj_res_bbl']:,.0f}")
    k4.metric("Production (res bbl)", f"{sel['prod_res_bbl']:,.0f}")
    ia = T.input_audit(pid)
    stored_audit = next((r for r in ia.get("audits", []) if r["vrr_date"] == period), None)
    if stored_audit:
        icon = {"REAL_SIGNAL": "✅", "DATA_ARTIFACT": "🛑", "INCONCLUSIVE": "⚠️"}.get(
            stored_audit["verdict"], "•")
        box = st.success if stored_audit["verdict"] == "REAL_SIGNAL" else st.warning
        box(f"{icon} Input audit: **{stored_audit['verdict']}** — {stored_audit['summary']}")
        if stored_audit["verdict"] != "REAL_SIGNAL":
            st.caption("Guardrail: no valve change may be proposed on this period — it "
                       "routes to the data steward instead (core/audit.py).")
    elif sel["any_extrapolated"]:
        st.warning("⚠️ This period used extrapolated/closest PVT lookups — inputs are "
                   "suspect. Run `make audit` to record a verdict.")

    base = alt.Chart(df).encode(x=alt.X("vrr_date:T", title=None))
    band_layer = alt.Chart(pd.DataFrame({"lo": [band[0]], "hi": [band[1]]})).mark_rect(
        opacity=0.12, color="green").encode(y="lo:Q", y2="hi:Q")
    target_layer = alt.Chart(pd.DataFrame({"t": [target]})).mark_rule(
        strokeDash=[6, 4], color="green").encode(y="t:Q")
    line = base.mark_line(point=True, strokeWidth=2).encode(
        y=alt.Y("vrr:Q", title="VRR", scale=alt.Scale(zero=False)),
        color=alt.value("#1f77b4"),
        tooltip=["vrr_date:T", alt.Tooltip("vrr:Q", format=".3f"),
                 alt.Tooltip("prod_res_bbl:Q", format=",.0f"),
                 alt.Tooltip("inj_res_bbl:Q", format=",.0f"),
                 "any_extrapolated:N", "n_completions:Q"])
    flags = base.transform_filter(alt.datum.any_extrapolated).mark_point(
        size=140, shape="triangle-up", color="orange", filled=True).encode(y="vrr:Q")
    marker = alt.Chart(pd.DataFrame({"d": [period]})).mark_rule(
        color="crimson", strokeDash=[3, 3]).encode(x="d:T")
    st.altair_chart((band_layer + target_layer + line + flags + marker).properties(height=340),
                    width="stretch")
    st.caption("Green band = the pattern's normal VRR band (vrr_agent.pattern_memory); "
               "dashed green = target; orange triangles = periods built on low-confidence "
               "PVT; red line = the period under review.")

    st.markdown("#### ΔVRR attribution")
    if prev:
        dec = T.vrr_decompose(pid, str(prev["vrr_date"]), str(period))
        if dec.get("ok"):
            c1, c2 = st.columns([2, 3])
            c1.markdown(
                f"**{prev['vrr_date']:%b %Y} → {period:%b %Y}**  \n"
                f"VRR {dec['vrr_a']:.3f} → {dec['vrr_b']:.3f} (**{dec['d_vrr']:+.3f}**)  \n"
                f"Injection side {dec['side_contributions']['injection']:+.4f} · "
                f"production side {dec['side_contributions']['production']:+.4f}  \n"
                f"_Exact log-mean (LMDI) split; Σ contributions = ΔVRR._")
            dd = pd.DataFrame(dec["drivers"])
            c2.altair_chart(
                alt.Chart(dd).mark_bar().encode(
                    x=alt.X("contribution:Q", title="ΔVRR contribution"),
                    y=alt.Y("label:N", sort="-x", title=None),
                    color=alt.condition(alt.datum.contribution > 0,
                                        alt.value("#d62728"), alt.value("#2ca02c")),
                    tooltip=[alt.Tooltip("contribution:Q", format="+.4f"),
                             alt.Tooltip("share:Q", format=".1%")]).properties(height=200),
                width="stretch")
        else:
            st.info(dec.get("reason", "no attribution"))
    else:
        st.info("No prior period in range to attribute against.")

    with st.expander("Monthly rows (vrr_curated.pattern_vrr, grain=monthly)"):
        st.dataframe(df, width="stretch", hide_index=True)

    # The draft belongs with the period it was computed from, not with the chat that
    # used to host it: the analysis above IS the evidence for this recommendation.
    st.divider()
    st.markdown("##### Draft a valve change for approval")
    case = AZ.analyze(pid, str(period))
    if not case.get("ok"):
        st.info(case.get("reason"))
    else:
        st.markdown(case["narrative"])
        if case.get("draft"):
            investigate = case["draft"]["action_type"] == "investigate_inputs"
            if st.button("📤 Submit to approval queue (stage: draft → analyst)",
                         type="primary"):
                res = T.submit_for_approval(pid, str(period), draft=case["draft"],
                                            submitted_by=user)
                st.success(f"Queued {res['action_id']} — next approver: {res['next_approver']}.")
            if investigate:
                st.caption("This draft is an *investigate inputs* item (suspect PVT), not a "
                           "valve change — it still routes through the same approval chain.")
        else:
            st.info("No anomaly fired for this period — nothing to draft.")

# --------------------------------------------------------------- Lineage ----
with tab_lineage:
    st.subheader(f"How {ctx['pattern_name']}'s {period:%b %Y} VRR was computed")
    audit = T.vrr_audit(pid, str(period))
    lin = T.vrr_lineage(pid, str(period))

    if audit.get("ok"):
        a1, a2, a3 = st.columns(3)
        a1.metric("Stored VRR", f"{audit['stored']['vrr']:.6f}",
                  f"run_id {audit['stored'].get('run_id')}", delta_color="off")
        a2.metric("Recomputed from raw", f"{audit['recomputed']['vrr']:.6f}",
                  f"{audit['n_raw_rows']} daily rows", delta_color="off")
        a3.metric("Difference", f"{audit['difference']:+.2e}",
                  "✅ verified" if audit["matches"] else "⚠️ mismatch",
                  delta_color="off")
        st.caption("Recomputed independently in this request from "
                   f"`{'`, `'.join(audit['provenance']['recomputed_from'])}` via "
                   f"`{audit['provenance']['code']}` — not read back from the curated table.")
        st.caption(f"PVT lookup methods in this period: **{', '.join(audit['pvt_methods'])}**"
                   + ("  ⚠️ low-confidence" if audit["low_confidence_inputs"] else ""))

    st.markdown("##### Derivation chain")
    st.code(" → ".join([lin["sources"]["raw_volumes"], lin["sources"]["pressure"],
                        lin["sources"]["pvt"], lin["sources"]["lineage_layer"],
                        lin["sources"]["aggregate"]]), language="text")
    st.markdown("##### Formulas (core.physics)")
    st.table(pd.DataFrame({"term": list(lin["formulas"]), "formula": list(lin["formulas"].values())}))

    st.markdown("##### Per-completion contributions (the lineage layer)")
    st.caption("One row per completion for this month: root inputs, the pattern pressure "
               "used, the PVT method that produced the FVFs, and every derived term.")
    st.dataframe(pd.DataFrame(lin["completions"]), width="stretch", hide_index=True)

    tot = lin["term_totals"]
    st.markdown("##### Roll-up")
    st.write(pd.DataFrame([{**tot,
                            "prod_res_bbl": lin["recomputed_from_terms"]["prod_res_bbl"],
                            "inj_res_bbl": lin["recomputed_from_terms"]["inj_res_bbl"],
                            "vrr": lin["recomputed_from_terms"]["vrr"]}]))
    st.caption("Unity Catalog OSS registers these tables as the catalog-of-record, so this "
               "chain is also visible as table-level lineage (`make register`).")

# -------------------------------------------------------------- Approval ----
with tab_approve:
    st.subheader("Approval queue")
    st.caption("Advisory only — the agent writes `draft`; every forward step is a human "
               "act (core.approval). Executing writes vrr_agent.adjustment_history, which "
               "is what the ρ (response factor) learning loop reads back.")
    stage = st.selectbox("Stage", AP.STAGES + ["rejected"], index=0)
    needed = APPROVER_FOR_STAGE.get(stage)
    drafts = q("SELECT * FROM vrr_agent.action_queue WHERE stage=%(s)s "
               "ORDER BY severity, created_at DESC", {"s": stage})
    if not drafts:
        st.info(f"No items in `{stage}`.")

    for d in drafts:
        head = (f"{d['pattern_name']} · {d['vrr_date']} · {d['action_type']} · "
                f"[{d['severity']}] · confidence {d['confidence']}")
        with st.expander(head, expanded=len(drafts) == 1):
            st.text(d.get("narrative") or "")
            rec = d.get("recommendation")
            if isinstance(rec, str):
                rec = json.loads(rec)
            if rec and rec.get("injector_changes"):
                st.dataframe(pd.DataFrame(rec["injector_changes"]),
                             width="stretch", hide_index=True)
            st.caption(f"action_id `{d['action_id']}` · driver `{d.get('driver')}` · "
                       f"raised by {d.get('stage_by')} · run {d.get('run_id')}")

            nxt = AP.next_stage(stage)
            if not nxt:
                st.caption("Terminal stage — no further transitions.")
                continue
            if role != needed:
                st.info(f"Stage `{stage}` advances on **{needed}** sign-off — you are "
                        f"acting as **{role}**. Switch role in the sidebar to action it.")
                continue
            c1, c2 = st.columns(2)
            if c1.button(f"✅ Approve → {nxt}", key=f"a{d['action_id']}", type="primary"):
                if nxt == "executed":
                    row = AP.build_adjustment_row(d, rec, approver=user)
                    execute(
                        "INSERT INTO vrr_agent.adjustment_history (action_id, id_pattern,"
                        " pattern_name, vrr_date, driver, anomaly, change_type,"
                        " d_inj_res_bbl, d_surface_pct, pre_vrr, predicted_post_vrr,"
                        " actual_post_vrr, decision, approved_by, outcome) VALUES"
                        " (%(action_id)s,%(id_pattern)s,%(pattern_name)s,%(vrr_date)s,"
                        " %(driver)s,%(anomaly)s,%(change_type)s,%(d_inj_res_bbl)s,"
                        " %(d_surface_pct)s,%(pre_vrr)s,%(predicted_post_vrr)s,"
                        " %(actual_post_vrr)s,%(decision)s,%(approved_by)s,%(outcome)s)", row)
                execute("UPDATE vrr_agent.action_queue SET stage=%(n)s, stage_by=%(u)s,"
                        " stage_ts=now() WHERE action_id=%(i)s",
                        {"n": nxt, "u": user, "i": d["action_id"]})
                st.rerun()
            if c2.button("❌ Reject", key=f"r{d['action_id']}"):
                execute("UPDATE vrr_agent.action_queue SET stage='rejected', stage_by=%(u)s,"
                        " stage_ts=now() WHERE action_id=%(i)s",
                        {"u": user, "i": d["action_id"]})
                st.rerun()

    st.divider()
    st.markdown("##### Executed adjustments (ρ-learning input)")
    st.dataframe(pd.DataFrame(q(
        "SELECT action_id, pattern_name, vrr_date, change_type, d_surface_pct, pre_vrr,"
        " predicted_post_vrr, actual_post_vrr, approved_by, ts FROM"
        " vrr_agent.adjustment_history ORDER BY ts DESC LIMIT 25")),
        width="stretch", hide_index=True)


# ------------------------------------------ Chat drawer (docked, every tab) ----
# Rendered into the `drawer` column, so it sits beside whichever tab is open instead of
# under it — you interrogate the chart without leaving the chart. The transcript is read
# from and written to `vrr_agent.chat_history`, so it survives a refresh and is shared:
# opening a pattern shows what anyone already asked about it.


@st.cache_resource
def history_ready() -> bool:
    """Create chat_history once per process. schema.sql only runs on a fresh volume, so
    this is what makes the table exist for an already-created database."""
    try:
        HIST.ensure_table()
        return True
    except Exception:
        return False            # drawer degrades to this-session-only; never raises


with drawer:
    if st.button("✕" if st.session_state.drawer_open else "💬", key="drawer_toggle",
                 width="stretch",
                 help="Ask the agent about whatever tab you are looking at"):
        st.session_state.drawer_open = not st.session_state.drawer_open
        st.rerun()

    if st.session_state.drawer_open:
        with st.container(key="vrr_chat_drawer", border=True):
            st.markdown(f"**💬 Ask about {ctx['pattern_name']}**")
            st.caption(f"{period:%b %Y} · numbers come from deterministic tools over "
                       "Postgres; the LLM only rephrases, behind the faithfulness gate.")
            agentic = st.toggle(
                "Model queries the tables itself", value=False, disabled=not llm_up,
                help="Off: the deterministic pipeline runs the tools and the model only "
                     "rewrites the result (~10 s). On: the model chooses which "
                     "tools/tables to query (~1–2 min on a local 7B) — still gated, and "
                     "rejected narration falls back to the computed one.")

            quick = {"Why this VRR?": f"Why is {ctx['pattern_name']}'s VRR "
                                      f"{'high' if rows[-1]['vrr'] > target else 'low'} "
                                      f"in {period:%B %Y}?",
                     "Is it correct?": f"Is the {period:%B %Y} number actually correct?",
                     "How computed?": f"How is {ctx['pattern_name']}'s VRR calculated?",
                     "Documents": "What do the documents say about changing injection rates?"}
            asked = None
            qa, qb = st.columns(2)
            for i, (short_label, prompt) in enumerate(quick.items()):
                if (qa if i % 2 == 0 else qb).button(short_label, width="stretch",
                                                     key=f"quick{i}"):
                    asked = prompt

            # Created now, filled at the end: inside a column `st.chat_input` renders
            # inline rather than pinned, so the answer can be written into a container
            # declared above it — no _pending_question round-trip, one run per question.
            transcript = st.container(height=420)
            typed = st.chat_input(f"Ask about {ctx['pattern_name']}…", key="drawer_input")
            asked = asked or typed

            durable = history_ready()
            if not durable:
                st.caption("⚠️ History unavailable (`vrr_agent.chat_history` could not be "
                           "created) — this conversation is session-only.")
                st.session_state.setdefault("chat_fallback", [])

            if asked:
                with st.spinner("Model is querying the tables…" if agentic
                                else "Running deterministic tools…"):
                    res = CH.respond(asked, pattern=pid, date=str(period), agentic=agentic)
                row = {"question": asked, "answer": res.get("text"),
                       "intent": res.get("intent"), "meta": res.get("meta") or {},
                       "payload": res.get("data") or {}, "asked_by": user}
                if durable:
                    try:            # a logging failure must never lose the answer
                        HIST.log_turn(pattern_id=pid, pattern_name=ctx["pattern_name"],
                                      date=str(period), question=asked, result=res,
                                      asked_by=user, agentic=agentic)
                    except Exception as exc:
                        st.caption(f"(not saved to history: {exc})")
                        st.session_state.setdefault("chat_fallback", []).append(row)
                else:
                    st.session_state.chat_fallback.append(row)

            # Read AFTER the write, in the same run: the new turn is in the list exactly
            # once, and a browser refresh renders the identical conversation.
            if durable:
                try:
                    turns = HIST.recent(pid, limit=50,
                                        since=st.session_state.get("history_hidden_before"))
                except Exception:
                    turns = []
            else:
                turns = st.session_state.get("chat_fallback", [])

            with transcript:
                if not turns:
                    st.caption("Nothing asked about this pattern yet.")
                for t in turns:
                    with st.chat_message("user"):
                        st.markdown(t["question"])
                        if t.get("asked_by"):
                            stamp = t.get("created_at")
                            st.caption(f"{t['asked_by']}"
                                       + (f" · {stamp:%d %b %H:%M}" if stamp else ""))
                    with st.chat_message("assistant"):
                        st.markdown(t.get("answer") or "_(no answer recorded)_")
                        meta = t.get("meta") or {}
                        st.caption(
                            f"intent: `{t.get('intent')}` · "
                            + (f"{meta.get('model', 'LLM')} · gate {meta.get('gate')}"
                               if meta.get("llm")
                               else f"computed answer ({meta.get('gate', 'no LLM')})")
                            + (f" · tools: {', '.join(meta['tools_called'])}"
                               if meta.get("tools_called") else ""))
                        if meta.get("violations"):
                            st.warning(f"Gate rejected the LLM phrasing: {meta['violations']}")
                        if t.get("payload"):
                            st.json(t["payload"], expanded=False)

            # Not a delete: the rows are shared, so one analyst must not be able to wipe
            # another's audit trail. This just moves a session-local cutoff forward.
            if turns and st.button("Hide history in this view", width="stretch"):
                st.session_state.history_hidden_before = datetime.now(timezone.utc)
                st.session_state.chat_fallback = []
                st.rerun()
