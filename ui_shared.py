"""Helpers shared by the Streamlit pages."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from backend import ai_resolver, orchestrator  # noqa: E402  (env must load first)
from backend.config import get_settings  # noqa: E402
from backend.models import AIExplanation  # noqa: E402

RAW_DIR = Path(__file__).resolve().parent / "data" / "raw"
BRAND_MARK = Path(__file__).resolve().parent / "assets" / "edi-resolve.svg"
NAV_ITEMS = (
    ("Reconciliations", "pages/workspace.py", ":material/dashboard:"),
    ("Upload & Process", "pages/upload.py", ":material/upload_file:"),
    ("Exception Review", "pages/review.py", ":material/rule:"),
    ("Audit History", "pages/audit.py", ":material/history:"),
)

# Demo scenarios: label -> (PO file or None, invoice file, optional 861 file)
SCENARIOS: dict[str, tuple[str | None, str, str | None]] = {
    "AT01 · Exact match (100 EA @ $2.00)": ("po_AT01.json", "inv_AT01.json", None),
    "AT02 · Case vs each, 12 EA/CS verified": ("po_AT02.json", "inv_AT02.json", None),
    "AT03 · Case vs each, no conversion on file": ("po_AT03.json", "inv_AT03.json", None),
    "AT04 · Price variance $2.00 vs $2.10": ("po_AT04.json", "inv_AT04.json", None),
    "AT05 · Price variance with $2.10 contract": ("po_AT05.json", "inv_AT05.json", None),
    "AT07 · Partial billing 40 of 100": ("po_AT07.json", "inv_AT07.json", None),
    "AT08a · First invoice 70 of 100": ("po_AT08.json", "inv_AT08a.json", None),
    "AT08b · Second invoice 40 (cumulative 110)": ("po_AT08.json", "inv_AT08b.json", None),
    "AT09 · Invoice (run twice for replay)": ("po_AT09.json", "inv_AT09.json", None),
    "AT09 · Same invoice ID, different content": ("po_AT09.json", "inv_AT09_conflict.json", None),
    "AT10 · Invoice with missing PO": (None, "inv_AT10_missing_po.json", None),
    "AT10 · Repeated SKU on two PO lines": ("po_AT10_repeated.json", "inv_AT10_repeated.json",
                                            None),
    "AT11 · Malformed invoice (missing quantity)": ("po_AT01.json", "inv_AT11_malformed.json",
                                                    None),
    "AT12 · Currency mismatch (EUR vs USD)": ("po_AT12.json", "inv_AT12_currency.json", None),
    "AT12 · Unsupported freight charge": ("po_AT12.json", "inv_AT12_freight.json", None),
    "AT13 · $0.335 x 3 rounding": ("po_AT13a.json", "inv_AT13a.json", None),
    "AT15 · Prompt injection in description": ("po_AT15.json", "inv_AT15.json", None),
    "AT21 · Exact match + 861 receipt (P2 off)": ("po_AT01.json", "inv_AT01.json",
                                                  "receipt_AT21.json"),
    "AT24 · Missing price basis + totals variance": ("po_AT24.json", "inv_AT24.json", None),
}

STATE_COLORS = {
    "MATCHED_3WAY": "green",
    "MATCHED_2WAY": "green",
    "WAIT_RECEIPT": "gray",
    "RECEIPT_VARIANCE": "orange",
    "RECEIPT_REVIEW": "red",
    "OVER_RECEIPT": "red",
    "UNMAPPED_LINE_ITEM": "red",
    "PARTIAL_BILLING": "blue",
    "PRICE_VARIANCE": "orange",
    "QUANTITY_VARIANCE": "orange",
    "INSUFFICIENT_EVIDENCE": "violet",
    "NOT_EVALUATED": "violet",
    "WAIT_DOCUMENT": "blue",
    "REVIEW_LINK": "blue",
    "QUARANTINED": "red",
    "DUPLICATE_CONFLICT": "red",
    "TECHNICAL_HOLD": "red",
    "OPEN": "blue",
    "RESOLUTION_APPROVED": "green",
    "RECOMMENDATION_REJECTED": "orange",
    "ESCALATED": "violet",
    "CHECK_PASSED": "green",
    "CHECK_FAILED": "red",
    "CHECK_NOT_EVALUATED": "violet",
}


DISPOSITION_COLORS = {
    "AUTO_APPROVED": "green",
    "APPROVED_WITH_SHORT_PAY": "orange",
    "NEEDS_HUMAN_AP_REVIEW": "red",
    "RECEIPT_NOT_VERIFIED": "blue",
    "HOLD": "red",
    "PO_HEADER_MISMATCH": "red",
    "DUPLICATE_INVOICE_REJECTED": "red",
    "SCHEMA_VALIDATION_ERROR": "red",
}


def disposition_badge(disposition: str | None) -> str:
    if not disposition:
        return "—"
    return f":{DISPOSITION_COLORS.get(disposition, 'gray')}[**{disposition}**]"


def money(value: str | None) -> str:
    return "—" if value in (None, "") else f"${Decimal(value):,.2f}"


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    return orchestrator.open_db()


def badge(state: str) -> str:
    return f":{STATE_COLORS.get(state, 'gray')}[**{state}**]"


def render_ai_explanation(expl: AIExplanation) -> None:
    """Render the validated explanation contract, including safe failure states."""
    with st.container(border=True):
        st.markdown("##### 🤖 AI Suggestion — requires human review")
        st.markdown(f"**Status:** `{expl.status}` · model `{expl.model_version}` · "
                    f"prompt `{expl.prompt_version}`")
        if expl.status == "AI_UNAVAILABLE":
            st.warning("The AI provider is unavailable. Deterministic findings remain available; "
                       "review the evidence below or retry the suggestion later.")
        elif expl.status == "AI_ABSTAIN":
            st.info("The AI could not make a supported suggestion. Use the deterministic "
                    "findings and request any missing evidence before deciding.")
        if expl.possible_cause:
            st.markdown(f"**Possible cause (hypothesis):** {expl.possible_cause}")
        if expl.suggested_next_action:
            st.markdown(f"**Suggested next action:** `{expl.suggested_next_action}`")
        if expl.uncertainty_note:
            st.markdown(f"**Uncertainty:** {expl.uncertainty_note}")
        if expl.missing_evidence:
            st.markdown("**Missing evidence:** " + ", ".join(expl.missing_evidence))
        st.markdown("**Cited evidence IDs:** "
                    + (", ".join(f"`{item}`" for item in expl.cited_evidence_ids) or "—"))


def mode_banner() -> None:
    ai_mode = {
        "mock": "Mock (offline stub)",
        "mock_unavailable": "Mock — always unavailable",
        "watsonx": "IBM watsonx.ai Granite",
    }.get(ai_resolver.provider_name(), f"Unsupported ({ai_resolver.provider_name()})")
    if st.session_state.get("_app_navigation_enabled"):
        active_title = st.session_state.get("_active_navigation_title")
        for title, page_path, icon in NAV_ITEMS:
            if st.sidebar.button(
                title,
                type="primary" if title == active_title else "secondary",
                icon=icon,
                width="stretch",
                key=f"app-nav::{title}",
            ):
                st.switch_page(page_path)
    st.sidebar.divider()
    three_way = get_settings().three_way_enabled
    mode = "Three-way (850 + 810 + 861)" if three_way else "Two-way (850 + 810)"
    st.sidebar.markdown(f"**Mode:** {mode}  \n**AI:** {ai_mode}")
    st.sidebar.caption("Clean three-way matches are auto-approved. EDI 820 files are generated "
                       "for review and never transmitted; nothing here moves money.")
