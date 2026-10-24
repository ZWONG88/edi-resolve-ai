"""Reconciliation workspace home page."""

from __future__ import annotations

import streamlit as st

from backend import orchestrator
from ui_shared import get_conn, mode_banner

mode_banner()
conn = get_conn()  # init_db() runs before any page renders

cases = orchestrator.list_cases(conn)
open_cases = [c for c in cases if c["review_status"] == "OPEN"
              and c["status"] != "MATCHED_2WAY"]
st.caption("RECONCILIATION WORKSPACE")
st.title("EDI reconciliation workspace")
st.markdown("Reconcile EDI 850 purchase orders against EDI 810 invoices, then review "
            "evidence and record a human decision.")

with st.container(border=True):
    c1, c2, c3 = st.columns(3)
    c1.metric("Cases", len(cases))
    c2.metric("Open cases for review", len(open_cases))
    c3.metric("Matched (2-way)", sum(c["status"] == "MATCHED_2WAY" for c in cases))

with st.container(border=True):
    st.subheader("Get started")
    nav = st.columns(3)
    nav[0].page_link("pages/upload.py", label="Upload & Process", icon=":material/upload_file:")
    nav[1].page_link("pages/review.py", label="Exception Review", icon=":material/search:")
    nav[2].page_link("pages/audit.py", label="Audit History", icon=":material/history:")

if cases:
    st.subheader("Recent cases")
    st.caption("Live records from the local reconciliation database.")
    st.dataframe(
        [{"Case": c["id"], "Result": c["status"], "Review": c["review_status"],
          "Disposition": c["disposition"] or "—", "Updated": c["updated_at"][:19]}
         for c in cases[:10]],
        hide_index=True,
        use_container_width=True,
    )

st.caption(
    "Deterministic rules use Decimal arithmetic. AI suggestions are evidence-grounded, "
    "clearly labeled, and always require human review. Receipt (861) matching is not evaluated "
    "in this P0 workspace."
)
