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
case_ids = [c["id"] for c in orchestrator.list_cases(conn)]
default = st.session_state.get("selected_case")
options = ["All cases", *case_ids]
choice = st.selectbox("Case", options,
                      index=options.index(default) if default in case_ids else 0)
rows = orchestrator.list_audit_events(conn, None if choice == "All cases" else choice)
if not rows:
    st.info("No audit events yet.")
    st.stop()

cols = ["event_at", "case_id", "event_type", "actor", "before_state", "after_state",
        "reviewer_reason", "escalation_owner", "recommendation_version", "idempotency_key",
        "policy_version", "reference_version", "source_pointers", "calculations_json",
        "doc_hashes"]
st.dataframe(pd.DataFrame([dict(r) for r in rows])[cols], hide_index=True,
             use_container_width=True)
