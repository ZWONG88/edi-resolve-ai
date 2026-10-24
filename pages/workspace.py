"""Reconciliation workspace home page."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import streamlit as st

from backend import orchestrator
from ui_shared import get_conn, mode_banner, money

mode_banner()
conn = get_conn()  # init_db() runs before any page renders

cases = orchestrator.list_cases(conn)
by_disposition = Counter(c["disposition_status"] for c in cases)


def total(column: str) -> str:
    return str(sum((Decimal(case[column]) for case in cases if case[column] is not None),
                   Decimal("0")))


st.caption("RECONCILIATION WORKSPACE")
st.title("EDI reconciliation workspace")
st.markdown("Reconcile EDI 850 purchase orders and 861 receipts against 810 invoices, then "
            "review evidence and record a human decision.")

with st.container(border=True):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Invoices", len(cases))
    c2.metric("Auto-approved", by_disposition.get("AUTO_APPROVED", 0))
    c3.metric("Short pay", by_disposition.get("APPROVED_WITH_SHORT_PAY", 0))
    c4.metric("Needs review / hold", len(cases) - by_disposition.get("AUTO_APPROVED", 0)
              - by_disposition.get("APPROVED_WITH_SHORT_PAY", 0))
    m1, m2, m3 = st.columns(3)
    m1.metric("Invoiced", money(total("invoiced_amount")))
    m2.metric("Approved to pay", money(total("approved_amount")))
    m3.metric("Blocked cash at risk", money(total("blocked_amount")))

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
          "Disposition": c["disposition_status"] or "—", "Updated": c["updated_at"][:19]}
         for c in cases[:10]],
        hide_index=True,
        use_container_width=True,
    )

st.caption(
    "Deterministic rules use Decimal arithmetic. AI suggestions are evidence-grounded and "
    "require human review. EDI 820 files are generated for review and never transmitted."
)
