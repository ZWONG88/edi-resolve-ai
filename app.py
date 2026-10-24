"""EDIResolve AI — Streamlit entry point (home page; other pages live in pages/)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import streamlit as st

from backend import orchestrator
from ui_shared import get_conn, mode_banner, money

st.set_page_config(page_title="EDIResolve AI", layout="wide")
conn = get_conn()  # init_db() runs before any page renders
mode_banner()

st.title("EDIResolve AI")
st.markdown(
    "Three-way reconciliation of **EDI 850 purchase orders**, **861 receiving advice** and "
    "**810 invoices**: clean invoices are auto-approved, discrepancies get a short-pay "
    "suggestion and an EDI 820 remittance, with evidence-grounded AI explanations."
)

cases = orchestrator.list_cases(conn)


def _sum(column: str) -> str:
    return str(sum((Decimal(c[column]) for c in cases if c[column] is not None), Decimal("0")))


by = Counter(c["disposition_status"] for c in cases)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Invoices", len(cases))
c2.metric("Auto-approved", by.get("AUTO_APPROVED", 0))
c3.metric("Short pay", by.get("APPROVED_WITH_SHORT_PAY", 0))
c4.metric("Needs review / hold", len(cases) - by.get("AUTO_APPROVED", 0)
          - by.get("APPROVED_WITH_SHORT_PAY", 0))
m1, m2, m3 = st.columns(3)
m1.metric("Invoiced", money(_sum("invoiced_amount")))
m2.metric("Approved to pay", money(_sum("approved_amount")))
m3.metric("Blocked cash at risk", money(_sum("blocked_amount")))

st.page_link("pages/upload.py", label="1. Upload & Process", icon="📥")
st.page_link("pages/review.py", label="2. Exception Review", icon="🔎")
st.page_link("pages/audit.py", label="3. Audit History", icon="📜")

st.caption(
    "Rules run on Decimal arithmetic. AI never does arithmetic, never sees raw EDI text, and "
    "every suggestion requires human review. 820 files are generated, never transmitted."
)
