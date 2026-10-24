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
from ui_shared import badge, disposition_badge, get_conn, mode_banner, money

mode_banner()
st.title("Exception Review")
conn = get_conn()
cases = orchestrator.list_cases(conn)
if not cases:
    st.info("No cases yet. Process a PO and invoice on the Upload & Process page.")
    st.stop()

MATCHED = ("MATCHED_2WAY", "MATCHED_3WAY")
FLAG_COLOR = "background-color: rgba(255, 75, 75, 0.18)"
WARN_COLOR = "background-color: rgba(255, 170, 0, 0.18)"

table = pd.DataFrame(
    [{"Case": c["id"], "Result": c["status"], "Disposition": c["disposition_status"] or "",
      "Review": c["review_status"], "Invoiced": money(c["invoiced_amount"]),
      "Blocked": money(c["blocked_amount"]), "Mode": c["mode"],
      "Updated": c["updated_at"][:19]} for c in cases]
)
show_auto = st.toggle("Show auto-approved cases", value=True)
if not show_auto:
    table = table[table["Disposition"] != "AUTO_APPROVED"]


def _row_style(row: pd.Series) -> list[str]:
    color = "" if row["Disposition"] == "AUTO_APPROVED" else (
        WARN_COLOR if row["Disposition"] == "APPROVED_WITH_SHORT_PAY" else FLAG_COLOR)
    return [color] * len(row)


st.dataframe(table.style.apply(_row_style, axis=1), hide_index=True,
             use_container_width=True)

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
st.markdown(f"### {result.get('invoice_id', case_id)} → PO {result.get('po_id', '—')}")

# Disposition banner (rules doc Stage 5)
dispo = case["disposition_status"]
amounts = (f"Invoiced {money(case['invoiced_amount'])} · approved "
           f"{money(case['approved_amount'])} · blocked {money(case['blocked_amount'])}")
if dispo == "AUTO_APPROVED":
    st.success(f"✅ AUTO_APPROVED — every 3-way check passed. {amounts}")
elif dispo == "APPROVED_WITH_SHORT_PAY":
    st.warning(f"⚠️ APPROVED_WITH_SHORT_PAY — discrepancies found; pay the approved amount "
               f"only. {amounts}")
elif dispo == "NEEDS_HUMAN_AP_REVIEW":
    st.error(f"🚩 NEEDS_HUMAN_AP_REVIEW — an AP reviewer must approve before the 820 is "
             f"generated. {amounts}")
elif dispo == "RECEIPT_NOT_VERIFIED":
    st.info(f"Two-way mode: receipt not verified, so no auto-approval or 820. {amounts}")
elif dispo:
    st.error(f"⛔ {dispo} — held; no payment can be computed until this is resolved.")

h1, h2, h3, h4 = st.columns(4)
h1.markdown(f"**Result**  \n{badge(case['status'])}")
h2.markdown(f"**Disposition**  \n{disposition_badge(dispo)}")
h3.markdown(f"**Review status**  \n{case['review_status']}"
            + (f"  \n_{case['disposition']}_" if case["disposition"] else ""))
h4.markdown(f"**Mode** {'three-way' if case['mode'] == '3way' else 'two-way'}  \n"
            f"evidence v{case['evidence_version']} · case v{case['version']}  \n"
            f"policy {case['policy_version'] or 'n/a'} · ref {case['reference_version'] or 'n/a'}")

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
for finding in result.get("header_findings", []):
    if finding.get("result_state"):
        st.error(f"**{finding['result_state']}** ({finding['check']}): {finding['message']}")
    elif finding["check"] == "invoice_total_de_minimis":
        st.info(finding["message"])

lines = result.get("line_results", [])
flagged = [ln for ln in lines if ln["status"] not in MATCHED or ln.get("de_minimis")
           or (ln.get("blocked_amount") not in (None, "0", "0.00"))]
if lines:
    st.subheader(f"Discrepancies ({len(flagged)} of {len(lines)} lines)")
    if not flagged:
        st.caption("No line discrepancies.")
    for ln in flagged:
        sku = ln["invoice_value_raw"].get("product_id", "")
        head = (f"**Line {ln['invoice_line_id']} · {sku} · {ln['status']}** — "
                f"blocked {money(ln.get('blocked_amount'))}")
        reasons = "; ".join(f["message"] for f in ln["findings"]
                            if f.get("result_state") and f["status"] != "CHECK_PASSED")
        text = f"{head}  \n{reasons}" + (f"  \n👉 {ln['correction']}" if ln.get("correction")
                                         else "")
        if ln.get("de_minimis"):
            st.info(f"**Line {ln['invoice_line_id']} · {sku}** — variance "
                    f"{money(ln.get('waived_amount'))} waived as DE_MINIMIS_PASSED.")
        elif ln["status"] in MATCHED or ln["status"] == "PARTIAL_BILLING":
            st.warning(text)
        else:
            st.error(text)

    def _cell(rec: dict, side: str, key: str) -> str:
        return rec.get(f"{side}_value_normalized", {}).get(key, "")

    rows = [{
        "Line": ln["invoice_line_id"],
        "SKU": ln["invoice_value_raw"].get("product_id", ""),
        "Status": ln["status"],
        "PO qty": _cell(ln, "po", "quantity"),
        "Billed qty": _cell(ln, "invoice", "quantity"),
        "Accepted qty": ln.get("accepted_qty") or "",
        "UOM": _cell(ln, "invoice", "uom"),
        "PO price": _cell(ln, "po", "unit_price"),
        "Invoice price": _cell(ln, "invoice", "unit_price"),
        "Invoiced $": ln.get("invoiced_amount") or "",
        "Approved $": ln.get("approved_amount") or "",
        "Blocked $": ln.get("blocked_amount") or "",
        "Adjustments": ", ".join(f"{k} {v}" for k, v in ln.get("adjustments", {}).items()),
    } for ln in lines]

    def _line_style(row: pd.Series) -> list[str]:
        bad = row["Status"] not in MATCHED and row["Status"] != "PARTIAL_BILLING"
        blocked = row["Blocked $"] not in ("", "0", "0.00")
        return [FLAG_COLOR if bad else WARN_COLOR if blocked else ""] * len(row)

    st.subheader("Line reconciliation (base UOM)")
    st.dataframe(pd.DataFrame(rows).style.apply(_line_style, axis=1), hide_index=True,
                 use_container_width=True)

    with st.expander("Original vs normalized values for one line"):
        pick = st.selectbox("Line", [ln["invoice_line_id"] for ln in lines])
        rec = next(ln for ln in lines if ln["invoice_line_id"] == pick)
        detail_rows = [{"Field": f, "PO": rec["po_value_raw"].get(f, ""),
                        "Invoice": rec["invoice_value_raw"].get(f, "")}
                       for f in ("quantity", "uom", "unit_price", "price_basis_qty",
                                 "extended_price")]
        detail_rows += [{"Field": f"{f} (normalized)",
                         "PO": rec["po_value_normalized"].get(f, "—"),
                         "Invoice": rec["invoice_value_normalized"].get(f, "—"),
                         "Delta": rec["delta"].get(f, "")}
                        for f in ("quantity", "unit_price", "extension")]
        st.dataframe(pd.DataFrame(detail_rows).fillna(""), hide_index=True,
                     use_container_width=True)

# EDI 820 remittance (generated, never transmitted)
if detail["remittance"]:
    remit = detail["remittance"]
    st.subheader("EDI 820 remittance")
    st.caption("Generated for AP review — NOT transmitted. Nothing in this app releases funds.")
    r = st.columns(3)
    r[0].metric("Invoiced", money(remit["invoiced_amount"]))
    r[1].metric("Adjustments", money(remit["adjustment_amount"]))
    r[2].metric("Paid", money(remit["paid_amount"]))
    st.download_button("Download 820 (JSON)", json.dumps(remit, indent=2),
                       file_name=f"820_{remit['invoice_number']}.json",
                       mime="application/json")
    with st.expander("820 payload"):
        st.json(remit)
elif dispo == "NEEDS_HUMAN_AP_REVIEW":
    st.caption("The short-pay 820 is generated when an AP reviewer approves this invoice.")

findings = detail["findings"]
if findings:
    with st.expander("All deterministic findings"):
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
if case["status"] in MATCHED:
    st.caption("All required checks passed; no AI explanation needed.")
elif expl is None:
    if st.button("Get AI suggestion"):
        with st.spinner("Requesting explanation..."):
            expl, _ = orchestrator.request_ai_explanation(conn, case_id)
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
                         ["", *orchestrator.ESCALATION_OWNERS])
    close = st.checkbox("Approve as a closing exception disposition (never becomes MATCHED)")
    use_rec = st.checkbox("This decision applies the AI suggestion shown above",
                          disabled=detail["recommendation_version"] is None)
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
