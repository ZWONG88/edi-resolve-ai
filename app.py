"""EDIResolve AI — Streamlit entry point (home page; other pages live in pages/)."""

from __future__ import annotations

import streamlit as st

from backend import orchestrator
from ui_shared import get_conn, mode_banner

st.set_page_config(page_title="EDIResolve AI", layout="wide")
conn = get_conn()  # init_db() runs before any page renders
mode_banner()

st.title("EDIResolve AI")
st.markdown(
    "Deterministic reconciliation of **EDI 850 purchase orders** against **EDI 810 "
    "invoices**, with evidence-grounded AI explanations for human reviewers."
)

cases = orchestrator.list_cases(conn)
open_cases = [c for c in cases if c["review_status"] == "OPEN" and c["status"] != "MATCHED_2WAY"]
with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    c1.metric("Cases", len(cases))
    c2.metric("Open exceptions", len(open_cases))
    c3.metric("Matched (2-way)", sum(c["status"] == "MATCHED_2WAY" for c in cases))

with st.container(border=True):
    st.subheader("Workspace")
    nav = st.columns(3)
    nav[0].page_link("pages/upload.py", label="Upload & Process", icon="📥")
    nav[1].page_link("pages/review.py", label="Exception Review", icon="🔎")
    nav[2].page_link("pages/audit.py", label="Audit History", icon="📜")

st.caption(
    "Rules run on Decimal arithmetic with zero tolerance. AI never does arithmetic, never sees "
    "raw EDI text, and every suggestion requires human review. No action here pays an invoice."
)
