"""Exception Review: inspect deterministic findings, request AI, record decisions."""

from __future__ import annotations

import json
import uuid

import pandas as pd
import streamlit as st

from backend import db, service
from backend.exceptions import (
    AuditPersistenceError,
    ParseError,
    ReviewValidationError,
    StaleVersionError,
)
from ui_shared import mode_banner, badge, get_conn

mode_banner()
st.title("Exception Review")
conn = get_conn()
cases = db.list_cases(conn)
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
detail = service.case_detail(conn, case_id)
case, result = detail["case"], detail["result"]

# The version the reviewer is looking at; actions against an older version are refused.
loaded_key = f"loaded_version::{case_id}"
if loaded_key not in st.session_state:
    st.session_state[loaded_key] = case["version"]
if case["version"] != st.session_state[loaded_key]:
    st.warning(service.STALE_CASE)
    if st.button("Refresh case"):
        st.session_state[loaded_key] = case["version"]
        st.rerun()

st.divider()
h1, h2, h3, h4 = st.columns(4)
h1.markdown(f"**Result**  \n{badge(case['status'])}")
h2.markdown(f"**Review status**  \n{case['review_status']}"
            + (f"  \n_{case['disposition']}_" if case["disposition"] else ""))
h3.markdown("**Mode**  \nTwo-way (P0) · receipt NOT_EVALUATED")
h4.markdown(f"**Versions**  \nevidence v{case['evidence_version']} · case v{case['version']}"
            f"  \npolicy {case['policy_version'] or 'n/a'}"
            f" · ref {case['reference_version'] or 'n/a'}")

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

# Line-level values: original vs normalized side by side
for rec in result.get("records", []):
    st.subheader(f"Invoice line {rec['invoice_line']} → PO line {rec['po_line'] or '—'}  "
                 f"{badge(rec['result_state'])}")
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
        st.json(result["evidence"])

# AI suggestion (on demand, cached per evidence version)
st.subheader("AI suggestion")
expl = detail["explanation"]
if case["status"] == "MATCHED_2WAY":
    st.caption("All required checks passed; no AI explanation needed.")
elif expl is None:
    if st.button("Get AI suggestion"):
        with st.spinner("Requesting explanation..."):
            expl, _ = service.request_ai_explanation(conn, case_id)
        if expl.status == "AI_UNAVAILABLE":
            st.warning(f"AI_UNAVAILABLE — {expl.uncertainty_note}")
        else:
            st.rerun()
if expl is not None:
    with st.container(border=True):
        st.markdown("##### 🤖 AI Suggestion — requires human review")
        st.markdown(f"**Status:** `{expl.status}` · model `{expl.model_version}` · "
                    f"prompt `{expl.prompt_version}`")
        if expl.possible_cause:
            st.markdown(f"**Possible cause (hypothesis):** {expl.possible_cause}")
        if expl.uncertainty_note:
            st.markdown(f"**Uncertainty:** {expl.uncertainty_note}")
        if expl.missing_evidence:
            st.markdown("**Missing evidence:** " + ", ".join(expl.missing_evidence))
        st.markdown(f"**Cited evidence:** {', '.join(expl.cited_evidence_ids) or '—'}")
        st.markdown(f"**Suggested next action:** `{expl.suggested_next_action}`")

# Review form
st.subheader("Review decision")
with st.form(f"review::{case_id}"):
    action = st.radio("Action", ["Approve", "Reject", "Escalate"], horizontal=True)
    reason = st.text_area("Reason (required)")
    owner = st.selectbox("Escalation owner (required for Escalate)",
                         ["", *service.ESCALATION_OWNERS])
    close = st.checkbox("Approve as a closing exception disposition (never becomes MATCHED)")
    use_rec = st.checkbox("This decision applies the AI suggestion shown above",
                          disabled=detail["recommendation_version"] is None)
    submitted = st.form_submit_button("Submit decision", type="primary")

if submitted:
    expected = st.session_state[loaded_key]
    key_name = f"idem::{case_id}::{expected}::{action}"
    st.session_state.setdefault(key_name, uuid.uuid4().hex)  # retries reuse the same key
    try:
        service.review_action(
            conn, case_id, action.lower(), reason, expected, st.session_state[key_name],
            owner=owner or None, close=close,
            recommendation_version=detail["recommendation_version"] if use_rec else None,
        )
    except ReviewValidationError as exc:
        st.error(str(exc))
    except StaleVersionError as exc:
        st.error(str(exc))
    except AuditPersistenceError:
        st.error(service.ACTION_NOT_SAVED)
    else:
        st.session_state[loaded_key] = expected + 1
        st.success(f"{action} recorded with audit event.")
        st.rerun()

# Correct and rerun
if case["invoice_id"]:
    with st.expander("Correct and rerun"):
        st.caption("Original bytes stay immutable. A corrected invoice is stored as a new "
                   "revision; checks rerun and earlier AI suggestions become stale.")
        corrected = st.file_uploader("Corrected EDI 810 (optional)", type="json",
                                     key=f"fix::{case_id}")
        if st.button("Rerun checks"):
            try:
                state = service.rerun_case(conn, case_id,
                                           corrected.getvalue() if corrected else None)
            except ParseError as exc:
                st.error(f"Upload failed: {exc.field_name} — {exc.message}")
            except AuditPersistenceError:
                st.error(service.ACTION_NOT_SAVED)
            else:
                st.session_state.pop(loaded_key, None)
                st.success(f"Rerun complete: {state}")
                st.rerun()
