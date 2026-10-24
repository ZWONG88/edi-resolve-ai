"""Upload & Process: ingest an 850 PO + 810 invoice (optional 861) and reconcile."""

from __future__ import annotations

import streamlit as st

from backend import orchestrator
from backend.exceptions import AuditPersistenceError
from ui_shared import RAW_DIR, SCENARIOS, badge, get_conn, mode_banner

mode_banner()
st.title("Upload & Process")
st.caption("Load an EDI 850 purchase order and an EDI 810 invoice. Deterministic rules run "
           "first; AI explanations are requested later, on demand.")

conn = get_conn()
po_bytes = inv_bytes = receipt_bytes = None
with st.container(border=True):
    st.subheader("Transaction documents")
    source = st.radio("Source", ["Sample scenario", "Upload files"], horizontal=True)

    if source == "Sample scenario":
        label = st.selectbox("Scenario", list(SCENARIOS))
        po_file, inv_file, receipt_file = SCENARIOS[label]
        st.caption(f"PO: `{po_file or '(none)'}` · Invoice: `{inv_file}`"
                   + (f" · Receipt: `{receipt_file}`" if receipt_file else ""))
        po_bytes = (RAW_DIR / po_file).read_bytes() if po_file else None
        inv_bytes = (RAW_DIR / inv_file).read_bytes()
        receipt_bytes = (RAW_DIR / receipt_file).read_bytes() if receipt_file else None
    else:
        c1, c2, c3 = st.columns(3)
        po_up = c1.file_uploader("EDI 850 purchase order (JSON)", type="json")
        inv_up = c2.file_uploader("EDI 810 invoice (JSON)", type="json")
        rc_up = c3.file_uploader("EDI 861 receipt (optional)", type="json")
        po_bytes = po_up.getvalue() if po_up else None
        inv_bytes = inv_up.getvalue() if inv_up else None
        receipt_bytes = rc_up.getvalue() if rc_up else None
        if rc_up:
            st.info("861 receipts are stored but unsupported for reconciliation while "
                    "THREE_WAY_ENABLED=false; the receipt check will show NOT_EVALUATED.")

if st.button("Process", type="primary", disabled=inv_bytes is None):
    try:
        with st.spinner("Parsing and running deterministic checks..."):
            out = orchestrator.process(conn, inv_bytes, po_bytes, receipt_bytes)
    except AuditPersistenceError:
        st.error(orchestrator.ACTION_NOT_SAVED)
    except Exception as exc:  # noqa: BLE001 - never show a traceback to the analyst
        st.error(f"Processing failed: {type(exc).__name__}. Nothing was saved.")
    else:
        st.session_state["selected_case"] = out.case_id
        if out.outcome == "QUARANTINED":
            st.error(f"Upload failed: {out.message}")
        elif out.outcome == "REPLAY_NOOP":
            st.info(f"Replay: {out.message} Case `{out.case_id}`.")
        elif out.outcome == "DUPLICATE_CONFLICT":
            st.warning(out.message)
        else:
            st.success(f"Case `{out.case_id}` created.")
            if out.message:
                st.info(out.message)
        st.markdown(f"Result: {badge(out.result_state)}")

if st.session_state.get("selected_case"):
    if st.button("Open in Exception Review →"):
        st.switch_page("pages/review.py")

with st.expander("Demo tools"):
    st.caption("Clears every case, document and audit event in the local demo database.")
    confirm = st.checkbox("I understand this deletes all local demo data")
    if st.button("Reset demo database", disabled=not confirm):
        orchestrator.reset_demo_data(conn)
        st.session_state.pop("selected_case", None)
        st.success("Demo database cleared.")
