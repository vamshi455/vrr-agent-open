"""VRR review + approval app (OSS) — Streamlit over PostgreSQL.

The report side reads vrr_curated for the verdict/trend/attribution; the approval
side moves action_queue drafts draft→analyst→rm→site→executed (core/approval.py)
and writes adjustment_history on execution — the human side of the ρ-learning loop.
Runs entirely local. `streamlit run src/vrr_agent_open/app/streamlit_app.py`.
"""
from __future__ import annotations

import psycopg
import streamlit as st

from vrr_agent_open.config import load_config
from vrr_agent_open.core import approval as AP

CFG = load_config()
st.set_page_config(page_title="VRR — Open", layout="wide")


def q(sql: str, params: dict | None = None) -> list[dict]:
    with psycopg.connect(CFG.pg_dsn, row_factory=psycopg.rows.dict_row) as c:
        with c.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall() if cur.description else []


tab_report, tab_approve = st.tabs(["📊 Report", "✅ Approval queue"])

with tab_report:
    st.title("VRR — Reasoning & Lineage")
    rows = q("SELECT pattern_id, pattern_name, vrr, vrr_date FROM vrr_curated.pattern_vrr_monthly "
             "ORDER BY pattern_id, vrr_date")
    if not rows:
        st.info("No VRR data yet — run `make seed`.")
    else:
        st.dataframe(rows, use_container_width=True)

with tab_approve:
    st.title("Approval queue")
    st.caption("Advisory only — agent drafts; humans approve draft → analyst → RM → site.")
    live = [s for s in AP.STAGES if s not in AP.TERMINAL]
    stage = st.selectbox("Stage", live + ["executed", "rejected"])
    drafts = q("SELECT * FROM vrr_agent.action_queue WHERE stage=%(s)s ORDER BY severity",
               {"s": stage})
    if not drafts:
        st.info(f"No items in {stage}.")
    for d in drafts:
        with st.expander(f"{d['pattern_name']} · {d['action_type']} · {d['confidence']}"):
            st.text(d.get("narrative") or "")
            nxt = AP.next_stage(stage)
            if nxt and st.button(f"Approve → {nxt}", key=f"a{d['action_id']}"):
                q("UPDATE vrr_agent.action_queue SET stage=%(n)s, stage_ts=now() WHERE action_id=%(i)s",
                  {"n": nxt, "i": d["action_id"]})
                st.rerun()
