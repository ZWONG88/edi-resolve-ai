"""Audit History: append-only event log."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from backend import orchestrator
from ui_shared import get_conn, mode_banner

mode_banner()
st.title("Audit History")
st.caption("Append-only. Every ingestion, rule run, AI result and human decision is recorded.")

conn = get_conn()
cases = orchestrator.list_cases(conn)
case_ids = [c["id"] for c in cases]
default = st.session_state.get("selected_case")
options = ["All cases", *case_ids]
choice = st.selectbox("Case", options,
                      index=options.index(default) if default in case_ids else 0)
rows = orchestrator.list_audit_events(conn, None if choice == "All cases" else choice)
if not rows:
    st.info("No audit events yet.")
    st.stop()

if choice != "All cases":
    selected = next((case for case in cases if case["id"] == choice), None)
    if selected:
        m1, m2, m3 = st.columns(3)
        m1.metric("Reconciliation result", selected["status"])
        m2.metric("Review status", selected["review_status"])
        m3.metric("Audit events", len(rows))
else:
    m1, m2 = st.columns(2)
    m1.metric("Cases", len(cases))
    m2.metric("Audit events", len(rows))

event_types = sorted({row["event_type"] for row in rows})
selected_types = st.multiselect("Event types", event_types, default=event_types)
rows = [row for row in rows if row["event_type"] in selected_types]
if not rows:
    st.info("No events match the selected event types.")
    st.stop()

cols = ["event_at", "case_id", "event_type", "actor", "before_state", "after_state",
        "reviewer_reason", "escalation_owner", "recommendation_version", "idempotency_key",
        "policy_version", "reference_version", "source_pointers", "calculations_json",
        "doc_hashes"]
with st.container(border=True):
    st.dataframe(pd.DataFrame([dict(r) for r in rows])[cols], hide_index=True,
                 use_container_width=True)
