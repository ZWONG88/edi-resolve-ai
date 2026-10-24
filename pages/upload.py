"""Upload & Process: import a dataset folder, or ingest one 850 + 810 (+ 861) and reconcile."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from backend import orchestrator
from backend.config import get_settings
from backend.exceptions import AuditPersistenceError
from ui_shared import (
    RAW_DIR,
    SCENARIOS,
    badge,
    disposition_badge,
    get_conn,
    mode_banner,
    money,
)

mode_banner()
st.title("Upload & Process")
st.caption("Load EDI 850 purchase orders, 861 receiving advice and 810 invoices. Deterministic "
           "rules run first; AI explanations are requested later, on demand.")

conn = get_conn()
three_way = get_settings().three_way_enabled
source = st.radio("Source", ["Import dataset folder", "Upload files", "Sample scenario"],
                  horizontal=True)


def show_summary(summary: dict) -> None:
    by = summary["by_disposition"]
    totals = summary["totals"]
    st.success(f"Processed {len(summary['cases'])} invoice case(s) "
               f"(850: {summary['counts']['850']}, 861: {summary['counts']['861']}, "
               f"810: {summary['counts']['810']}).")
    c = st.columns(4)
    c[0].metric("Auto-approved", by.get("AUTO_APPROVED", 0))
    c[1].metric("Approved with short pay", by.get("APPROVED_WITH_SHORT_PAY", 0))
    c[2].metric("Needs AP review", by.get("NEEDS_HUMAN_AP_REVIEW", 0))
    c[3].metric("On hold", sum(v for k, v in by.items() if k not in (
        "AUTO_APPROVED", "APPROVED_WITH_SHORT_PAY", "NEEDS_HUMAN_AP_REVIEW")))
    m = st.columns(4)
    m[0].metric("Invoiced", money(totals["invoiced"]))
    m[1].metric("Approved to pay", money(totals["approved"]))
    m[2].metric("Blocked cash at risk", money(totals["blocked"]))
    m[3].metric("Held (not evaluated)", money(totals["held"]))
    frame = pd.DataFrame(summary["cases"])
    if not frame.empty:
        st.dataframe(frame, hide_index=True, use_container_width=True)
    for note in summary.get("skipped", []):
        st.warning(f"Skipped {note}")


if source == "Import dataset folder":
    folders = orchestrator.dataset_folders(RAW_DIR)
    if not folders:
        st.info(f"No dataset folders found under `{RAW_DIR}`. Add a folder of team-format "
                "EDI JSON files (850, 861, 810).")
    else:
        by_name = {f.name: f for f in folders}
        folder = by_name[st.selectbox("Dataset folder (data/raw/…)", list(by_name))]
        st.caption(", ".join(f.name for f in sorted(folder.glob("*.json"))))
        if not three_way:
            st.info("THREE_WAY_ENABLED=false: invoices are matched two-way only; no "
                    "auto-approval or 820 generation.")
        if st.button("Import & reconcile", type="primary"):
            try:
                with st.spinner("Importing documents and running 3-way checks..."):
                    summary = orchestrator.import_dataset(conn, folder, sandbox=True)
            except AuditPersistenceError:
                st.error(orchestrator.ACTION_NOT_SAVED)
            except Exception as exc:  # noqa: BLE001 - never show a traceback to the analyst
                st.error(f"Import failed: {type(exc).__name__}: {exc}")
            else:
                st.session_state["last_import"] = summary
                if summary["cases"]:
                    st.session_state["selected_case"] = summary["cases"][0]["case_id"]
        if "last_import" in st.session_state:
            show_summary(st.session_state["last_import"])
else:
    po_bytes = inv_bytes = receipt_bytes = None
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
        rc_up = c3.file_uploader("EDI 861 receiving advice (JSON)", type="json")
        po_bytes = po_up.getvalue() if po_up else None
        inv_bytes = inv_up.getvalue() if inv_up else None
        receipt_bytes = rc_up.getvalue() if rc_up else None
        if rc_up and not three_way:
            st.info("861 receipts are stored but unsupported for reconciliation while "
                    "THREE_WAY_ENABLED=false; the receipt check will show NOT_EVALUATED.")

    if st.button("Process", type="primary", disabled=inv_bytes is None):
        try:
            with st.spinner("Parsing and running deterministic checks..."):
                out = orchestrator.process(conn, inv_bytes, po_bytes, receipt_bytes,
                                           sandbox=source == "Sample scenario")
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
            st.markdown(f"Result: {badge(out.result_state)} · "
                        f"Disposition: {disposition_badge(out.disposition)}")

if st.session_state.get("selected_case"):
    if st.button("Open in Exception Review →"):
        st.switch_page("pages/review.py")

with st.expander("Demo tools"):
    st.caption("Clears test/sample cases only (sample scenarios and dataset-folder imports). "
               "Uploaded (real) cases and their audit history are kept.")
    confirm = st.checkbox("I understand this deletes all test/sample data")
    if st.button("Reset test data", disabled=not confirm):
        try:
            counts = orchestrator.reset_test_data(conn)
        except AuditPersistenceError:
            st.error(orchestrator.ACTION_NOT_SAVED)
        else:
            st.session_state.pop("selected_case", None)
            st.session_state.pop("last_import", None)
            st.success(f"Test data cleared: {counts['cases']} case(s), "
                       f"{counts['documents']} document(s) removed; real cases kept.")
