"""Helpers shared by the Streamlit pages."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from backend import ai_resolver, db  # noqa: E402  (env must load first)

RAW_DIR = Path(__file__).resolve().parent / "data" / "raw"

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
    "MATCHED_2WAY": "green",
    "PARTIAL_BILLING": "blue",
    "PRICE_VARIANCE": "orange",
    "QUANTITY_VARIANCE": "orange",
    "INSUFFICIENT_EVIDENCE": "violet",
    "NOT_EVALUATED": "violet",
    "WAIT_DOCUMENT": "gray",
    "REVIEW_LINK": "gray",
    "QUARANTINED": "red",
    "DUPLICATE_CONFLICT": "red",
    "TECHNICAL_HOLD": "red",
}


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    return db.init_db()


def badge(state: str) -> str:
    return f":{STATE_COLORS.get(state, 'gray')}[**{state}**]"


def mode_banner() -> None:
    ai_mode = "Stub (offline)" if ai_resolver.stub_mode_enabled() else "IBM watsonx.ai Granite"
    st.sidebar.markdown(f"**Mode:** Two-way (P0)  \n**AI:** {ai_mode}")
    st.sidebar.caption("Receipt (861) matching is disabled. No action here pays an invoice "
                       "or changes an external system.")
