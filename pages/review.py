"""Exception Review: inspect deterministic findings, request AI, record decisions."""

from __future__ import annotations

import json
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
from backend.models import ReviewActionType, ReviewRequest
from ui_shared import badge, get_conn, mode_banner, render_ai_explanation

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

st.divider()
with st.container(border=True):
    st.markdown(f"**Case ID**  `{case_id}`")
    h1, h2, h3, h4 = st.columns(4)
    h1.markdown(f"**Result**  \n{badge(case['status'])}")
    h2.markdown(f"**Review status**  \n{badge(case['review_status'])}"
                + (f"  \n_{case['disposition']}_" if case["disposition"] else ""))
    h3.markdown("**Mode**  \nTwo-way (P0) · receipt NOT_EVALUATED")
    h4.markdown(f"**Versions**  \nevidence v{case['evidence_version']} · "
                f"case v{case['version']}"
                f"  \npolicy {case['policy_version'] or 'n/a'}"
                f" · ref {case['reference_version'] or 'n/a'}")

st.caption("Review status tracks the human decision; the result state above always reflects "
           "the deterministic reconciliation checks.")

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

# Line deltas first, so reviewers can scan discrepancies before the raw values.
line_results = result.get("line_results", [])
if line_results:
    with st.container(border=True):
        st.subheader("Discrepancy summary")
        st.caption("Deltas are shown as returned by the rules engine. A blank delta means that "
                   "check was not evaluated or did not produce a difference.")
        summary_rows = []
        for rec in line_results:
            delta = rec.get("delta", {})
            summary_rows.append({
                "Invoice line": rec.get("invoice_line_id", "—"),
                "PO line": rec.get("po_line_id") or "—",
                "Line result": rec.get("status", "—"),
                "Unit price delta": delta.get("unit_price", "—"),
                "Extension delta": delta.get("extension", "—"),
                "Quantity delta": delta.get("quantity", "—"),
                "Open PO balance": delta.get("po_balance", "—"),
            })
        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

# Line-level values: original vs normalized side by side
for rec in line_results:
    st.subheader(f"Invoice line {rec['invoice_line_id']} → PO line "
                 f"{rec['po_line_id'] or '—'}  {badge(rec['status'])}")
    fields = ["quantity", "uom", "unit_price", "price_basis_qty", "extended_price"]
    rows = []
    for field in fields:
        rows.append({
            "Field": field,
            "PO original": rec["po_value_raw"].get(field, ""),
            "Invoice original": rec["invoice_value_raw"].get(field, ""),
        })
    for field in ("quantity", "unit_price", "extension"):
        rows.append({
            "Field": f"{field} (normalized)",
            "PO original": rec["po_value_normalized"].get(field, "—"),
            "Invoice original": rec["invoice_value_normalized"].get(field, "—"),
            "Delta": rec["delta"].get(field, ""),
        })
    if "po_balance" in rec["delta"]:
        rows.append({"Field": "open PO balance", "PO original": rec["delta"]["po_balance"]})
    st.dataframe(pd.DataFrame(rows).fillna("").rename(
        columns={"PO original": "PO", "Invoice original": "Invoice"}),
        hide_index=True, use_container_width=True)

findings = detail["findings"]
if findings:
    st.subheader("Deterministic findings")
    evaluated = [f for f in findings if f["check_status"] != "CHECK_NOT_EVALUATED"]
    not_evaluated = [f for f in findings if f["check_status"] == "CHECK_NOT_EVALUATED"]
    cols = ["check_name", "check_status", "finding_type", "invoice_line", "delta",
            "tolerance_applied", "message", "evidence_json"]
    st.dataframe(pd.DataFrame(evaluated)[cols] if evaluated else pd.DataFrame(),
                 hide_index=True, use_container_width=True)
    if not_evaluated:
        st.markdown("**Checks NOT evaluated** (not a pass, not a failure)")
        st.dataframe(pd.DataFrame(not_evaluated)[["check_name", "finding_type", "message"]],
                     hide_index=True, use_container_width=True)
if result.get("evidence"):
    with st.expander("Evidence (source pointers and values)"):
        st.dataframe(pd.DataFrame(result["evidence"]), hide_index=True,
                     use_container_width=True)

# AI suggestion (on demand, cached per evidence version)
st.subheader("AI suggestion")
expl = detail["explanation"]
ai_error = None
if case["status"] == "MATCHED_2WAY":
    st.caption("All required checks passed; no AI explanation needed.")
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
    st.warning("AI suggestion could not be retrieved. Deterministic findings and evidence "
               "are still available; retry later or continue with human review.")
    st.caption(f"Technical detail: {ai_error}")
if expl is not None:
    render_ai_explanation(expl)

# Review form
st.subheader("Review decision")
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
    st.subheader("Reviewer history")
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
