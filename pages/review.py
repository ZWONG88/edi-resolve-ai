"""Exception Review: inspect deterministic findings, request AI, record decisions."""

from __future__ import annotations

import json
import sqlite3
import uuid

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from backend import orchestrator
from backend.exceptions import (
    AuditPersistenceError,
    ParseError,
    ReviewValidationError,
    StaleVersionError,
)
from backend.models import AIExplanation, ReviewActionType, ReviewRequest
from ui_shared import badge, get_conn, mode_banner, render_ai_explanation


def render_case_ai(
    conn: sqlite3.Connection,
    case_id: str,
    case_status: str,
    explanation: AIExplanation | None,
) -> None:
    """Render an optional explanation with a safe provider-failure fallback."""
    with st.container(border=True):
        st.subheader("AI reasoning")
        expl = explanation
        ai_error = None
        if case_status == "MATCHED_2WAY":
            st.info("Required two-way checks passed; no AI explanation is needed.")
        elif expl is None:
            if st.button("Get AI suggestion"):
                with st.spinner("Requesting explanation..."):
                    try:
                        expl, _ = orchestrator.request_ai_explanation(conn, case_id)
                    except AuditPersistenceError:
                        st.error(orchestrator.ACTION_NOT_SAVED)
                    except Exception as exc:  # noqa: BLE001 - provider failures must not break review
                        ai_error = type(exc).__name__
                    else:
                        if expl.status != "AI_UNAVAILABLE":
                            st.rerun()
        if ai_error:
            st.warning("AI suggestion could not be retrieved. Deterministic findings and "
                       "evidence are still available; retry later or continue with human review.")
            st.caption(f"Technical detail: {ai_error}")
        if expl is not None:
            render_ai_explanation(expl)
        elif case_status != "MATCHED_2WAY":
            st.info("Request an explanation to see possible cause, recommendation, cited "
                    "evidence, and uncertainty. Deterministic results remain available.")


mode_banner()
st.title("Exception Review")
conn = get_conn()
cases = orchestrator.list_cases(conn)
if not cases:
    st.info("No cases yet. Process a PO and invoice on the Upload & Process page.")
    st.stop()

table = pd.DataFrame(
    [{"Case": c["id"], "Result": c["status"], "Review": c["review_status"],
      "Disposition": c["disposition"] or "", "Evidence v": c["evidence_version"],
      "Updated": c["updated_at"][:19]} for c in cases]
)
show_matched = st.toggle("Show matched cases", value=True)
if not show_matched:
    table = table[table["Result"] != "MATCHED_2WAY"]
st.dataframe(table, hide_index=True, use_container_width=True)

ids = list(table["Case"])
if not ids:
    st.stop()
default = st.session_state.get("selected_case")
case_id = st.selectbox("Open case", ids, index=ids.index(default) if default in ids else 0)
st.session_state["selected_case"] = case_id
detail = orchestrator.case_detail(conn, case_id)
case, result = detail["case"], detail["result"]

# The version the reviewer is looking at; actions against an older version are refused.
loaded_key = f"loaded_version::{case_id}"
if loaded_key not in st.session_state:
    st.session_state[loaded_key] = case["version"]
if case["version"] != st.session_state[loaded_key]:
    st.warning(orchestrator.STALE_CASE)
    if st.button("Refresh case"):
        st.session_state[loaded_key] = case["version"]
        st.rerun()

st.caption("RECONCILIATIONS / CASE")
po_identifier = result.get("po_id") or case["po_id"] or "—"
invoice_identifier = result.get("invoice_id") or case["invoice_id"] or "—"
evidence = result.get("evidence", [])
invoice_total = next((item["raw_value"] for item in evidence
                      if item["evidence_id"] == "inv.total_amount"), None)
invoice_currency = next((item["raw_value"] for item in evidence
                         if item["evidence_id"] == "inv.currency"), None)
invoice_total_display = (
    f"{invoice_currency} {invoice_total}" if invoice_total is not None else "Not provided"
)
with st.container(border=True):
    st.markdown(f"### `{case_id}`")
    h1, h2, h3 = st.columns(3)
    h1.markdown(f"**Result:** {badge(case['status'])}  \n"
                f"**Human review:** {badge(case['review_status'])}"
                + (f"  \n_{case['disposition']}_" if case["disposition"] else ""))
    h2.markdown(f"**Documents**  \nPO `{po_identifier}`  \nInvoice `{invoice_identifier}`")
    h3.markdown(f"**Invoice total · source value**  \n`{invoice_total_display}`")
    st.caption(f"Evidence v{case['evidence_version']} · case v{case['version']} · "
               f"policy {case['policy_version'] or 'n/a'} · "
               f"reference {case['reference_version'] or 'n/a'}")

st.caption("Review status tracks the human decision; the result state above always reflects "
           "the deterministic reconciliation checks. Matching never records human approval.")

# Non-comparison outcomes
if "error" in result:
    err = result["error"]
    st.error(f"Quarantined — `{err['field']}`: {err['message']} (value {err['raw_value']})")
if "missing" in result:
    st.warning(f"Waiting for document: {result['missing']}")
if "conflict" in result:
    st.error(f"Duplicate conflict: {json.dumps(result['conflict'], indent=1)}")
if "errors" in result:
    st.error(f"Technical hold: {result.get('message')}  \n" + "  \n".join(result["errors"]))
for note in result.get("notes", []):
    st.info(note)

findings = detail["findings"]
audit_groups = (
    ("Price", {"unit_price", "extension_vs_po", "price_basis", "contract_reference"}),
    ("Quantity", {"quantity"}),
    ("UOM normalization", {"uom_conversion"}),
    ("Line presence", {"line_link"}),
    ("Invoice checks", {"currency", "invoice_extension_arithmetic", "invoice_total"}),
    ("Receipt verification", {"receipt"}),
)
pipeline = []
for label, check_names in audit_groups:
    statuses = [finding["check_status"] for finding in findings
                if finding["check_name"] in check_names]
    if statuses:
        if "CHECK_FAILED" in statuses:
            status = "CHECK_FAILED"
        elif "CHECK_NOT_EVALUATED" in statuses:
            status = "CHECK_NOT_EVALUATED"
        else:
            status = "CHECK_PASSED"
        pipeline.append((label, status))
if pipeline:
    st.caption("DETERMINISTIC AUDIT PIPELINE")
    for start in range(0, len(pipeline), 3):
        pipeline_row = pipeline[start:start + 3]
        pipeline_cols = st.columns(len(pipeline_row))
        for col, (label, status) in zip(pipeline_cols, pipeline_row, strict=True):
            with col, st.container(border=True):
                st.markdown(f"**{label}**")
                st.markdown(badge(status))

line_results = result.get("line_results", [])
if line_results:
    st.subheader("Discrepancy summary")
    st.caption("Values and deltas are shown as returned by the deterministic rules engine. "
               "Blank deltas mean the check was not evaluated or found no difference.")
    summary_rows = []
    for rec in line_results:
        delta = rec.get("delta", {})
        summary_rows.append({
            "Invoice line": rec.get("invoice_line_id", "—"),
            "PO line": rec.get("po_line_id") or "—",
            "Result": rec.get("status", "—"),
            "Unit price Δ": delta.get("unit_price", "—"),
            "Extension Δ": delta.get("extension", "—"),
            "Quantity Δ": delta.get("quantity", "—"),
        })
    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    line_labels = [f"{rec['invoice_line_id']} → PO {rec['po_line_id'] or 'unlinked'} · "
                   f"{rec['status']}" for rec in line_results]
    selected_label = st.selectbox("Inspect line evidence", line_labels)
    rec = line_results[line_labels.index(selected_label)]
    audit_col, ai_col = st.columns([1.1, 1], gap="large")

    with audit_col, st.container(border=True):
        st.subheader("Line-item audit")
        st.markdown(f"Invoice line `{rec['invoice_line_id']}` → PO line "
                    f"`{rec['po_line_id'] or '—'}`  {badge(rec['status'])}")
        po_col, invoice_col = st.columns(2)
        fields = ["quantity", "uom", "unit_price", "price_basis_qty", "extended_price"]
        with po_col:
            st.markdown("**Purchase order evidence**")
            for field in fields:
                value = rec["po_value_raw"].get(field)
                if value is not None:
                    st.caption(field.replace("_", " "))
                    st.code(value, language=None)
        with invoice_col:
            st.markdown("**Invoice evidence**")
            for field in fields:
                value = rec["invoice_value_raw"].get(field)
                if value is not None:
                    st.caption(field.replace("_", " "))
                    st.code(value, language=None)
        normalized_rows = []
        for field in ("quantity", "unit_price", "extension"):
            normalized_rows.append({
                "Normalized check": field.replace("_", " "),
                "PO": rec["po_value_normalized"].get(field, "—"),
                "Invoice": rec["invoice_value_normalized"].get(field, "—"),
                "Delta": rec["delta"].get(field, "—"),
            })
        st.dataframe(pd.DataFrame(normalized_rows), hide_index=True,
                     use_container_width=True)

    with ai_col:
        render_case_ai(conn, case_id, case["status"], detail["explanation"])
else:
    render_case_ai(conn, case_id, case["status"], detail["explanation"])

if findings:
    evaluated = [f for f in findings if f["check_status"] != "CHECK_NOT_EVALUATED"]
    not_evaluated = [f for f in findings if f["check_status"] == "CHECK_NOT_EVALUATED"]
    cols = ["check_name", "check_status", "finding_type", "invoice_line", "delta",
            "tolerance_applied", "message", "evidence_json"]
    with st.expander("Deterministic findings", expanded=False):
        st.caption("NOT_EVALUATED is neither a pass nor a failure.")
        st.dataframe(pd.DataFrame(evaluated)[cols] if evaluated else pd.DataFrame(),
                     hide_index=True, use_container_width=True)
        if not_evaluated:
            st.markdown("**Checks NOT_EVALUATED**")
            st.dataframe(pd.DataFrame(not_evaluated)[["check_name", "finding_type", "message"]],
                         hide_index=True, use_container_width=True)
if result.get("evidence"):
    with st.expander("Evidence index", expanded=False):
        st.dataframe(pd.DataFrame(result["evidence"]), hide_index=True,
                     use_container_width=True)

# Review form
st.subheader("Review decision")
with st.container(border=True):
    st.caption("Human review is required. Approve, Reject, and Escalate require a reason; "
               "Escalate also requires an owner.")
    with st.form(f"review::{case_id}"):
        action = st.radio("Action", ["Approve", "Reject", "Escalate"], horizontal=True)
        reason = st.text_area("Reason (required)")
        owner = st.selectbox("Escalation owner (required for Escalate)",
                             ["", *orchestrator.ESCALATION_OWNERS])
        close = st.checkbox("Approve as a closing exception disposition (never becomes MATCHED)")
        use_rec = st.checkbox(
            "Record that this decision applies the AI suggestion shown above",
            disabled=detail["recommendation_version"] is None,
            help="Links this review action to the validated suggestion version in the audit log.",
        )
        submitted = st.form_submit_button("Submit decision", type="primary")

if submitted:
    expected = st.session_state[loaded_key]
    key_name = f"idem::{case_id}::{expected}::{action}"
    st.session_state.setdefault(key_name, uuid.uuid4().hex)  # retries reuse the same key
    try:
        request = ReviewRequest(
            case_id=case_id, case_version=expected, action=ReviewActionType(action.upper()),
            reason=reason, escalation_owner=owner or None,
            idempotency_key=st.session_state[key_name], close=close,
            recommendation_version=detail["recommendation_version"] if use_rec else None,
        )
        orchestrator.review_action(conn, request)
    except ValidationError as exc:
        st.error("; ".join(err["msg"].removeprefix("Value error, ") for err in exc.errors()))
    except ReviewValidationError as exc:
        st.error(str(exc))
    except StaleVersionError as exc:
        st.error(str(exc))
    except AuditPersistenceError:
        st.error(orchestrator.ACTION_NOT_SAVED)
    else:
        st.session_state[loaded_key] = expected + 1
        st.success(f"{action} recorded with audit event.")
        st.rerun()

if detail["reviews"]:
    with st.expander("Reviewer history", expanded=False):
        review_rows = [{
            "Recorded at": row["event_at"][:19],
            "Action": row["action"],
            "Reason": row["reason"],
            "Escalation owner": row["escalation_owner"] or "—",
            "Case version": row["case_version"],
        } for row in detail["reviews"]]
        st.dataframe(pd.DataFrame(review_rows), hide_index=True, use_container_width=True)

# Correct and rerun
if case["invoice_id"]:
    with st.expander("Correct and rerun"):
        st.caption("Original bytes stay immutable. A corrected invoice is stored as a new "
                   "revision; checks rerun and earlier AI suggestions become stale.")
        corrected = st.file_uploader("Corrected EDI 810 (optional)", type="json",
                                     key=f"fix::{case_id}")
        if st.button("Rerun checks"):
            try:
                state = orchestrator.rerun_case(conn, case_id,
                                           corrected.getvalue() if corrected else None)
            except ParseError as exc:
                st.error(f"Upload failed: {exc.field_name} — {exc.message}")
            except AuditPersistenceError:
                st.error(orchestrator.ACTION_NOT_SAVED)
            else:
                st.session_state.pop(loaded_key, None)
                st.success(f"Rerun complete: {state}")
                st.rerun()
