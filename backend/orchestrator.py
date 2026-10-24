"""Orchestrator: ingest, reconcile, explain, review, rerun.

The single backend entry point for the Streamlit frontend (PR #1 backend plan).
Pages import only this module. Every state change and its audit event are committed
in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import ai_resolver, comparator, db, parser, reference
from backend.allocation import get_prior_allocations
from backend.config import get_settings
from backend.exceptions import ParseError, ReviewValidationError, StaleVersionError
from backend.models import (
    BLOCKING_STATES,
    AIExplanation,
    AuditEvent,
    CaseResult,
    FindingStatus,
    Invoice,
    PurchaseOrder,
    ReconciliationPolicy,
    ReviewActionType,
    ReviewRecord,
    ReviewRequest,
    ReviewStatus,
)

Source = str | Path | bytes

ACTION_NOT_SAVED = "Action not saved. Please retry."
STALE_CASE = "Case was updated by another action. Please refresh."
ESCALATION_OWNERS = (
    "operations manager",
    "pricing owner",
    "product-data owner",
    "receiving owner",
)
REVIEW_OUTCOMES = {
    ReviewActionType.APPROVE: ReviewStatus.RESOLUTION_APPROVED,
    ReviewActionType.REJECT: ReviewStatus.RECOMMENDATION_REJECTED,
    ReviewActionType.ESCALATE: ReviewStatus.ESCALATED,
}


class IngestOutcome:
    """Result of process(): which case, and what happened to the upload."""

    def __init__(self, case_id: str, outcome: str, result_state: str, message: str = "") -> None:
        self.case_id = case_id
        self.outcome = outcome  # CREATED, REPLAY_NOOP, QUARANTINED, DUPLICATE_CONFLICT
        self.result_state = result_state
        self.message = message


def _bytes(src: Source) -> bytes:
    return src if isinstance(src, bytes) else Path(src).read_bytes()


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def result_to_dict(result: CaseResult) -> dict[str, Any]:
    data = result.model_dump(mode="json")
    data["evidence_index"] = result.evidence_map()
    data["not_evaluated_checks"] = result.not_evaluated_checks()
    data["ai_bundle"] = ai_resolver.build_evidence_bundle(result)
    return data


def _audit(conn: sqlite3.Connection, **fields: Any) -> str:
    return db.write_audit_event(conn, AuditEvent(**fields))


# ------------------------------------------------------------------ read API


def open_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database (creating tables if needed). Defaults to DATABASE_PATH."""
    return db.init_db(path)


def list_cases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.list_cases(conn)


def list_audit_events(conn: sqlite3.Connection, case_id: str | None = None) -> list[sqlite3.Row]:
    return db.list_audit_events(conn, case_id)


def list_review_records(conn: sqlite3.Connection, case_id: str) -> list[sqlite3.Row]:
    return db.list_review_records(conn, case_id)


def reset_demo_data(conn: sqlite3.Connection) -> None:
    """Clear the local demo database (Upload page demo tools)."""
    with db.transaction(conn):
        for table in ("review_records", "audit_events", "ai_explanations", "allocations",
                      "case_findings", "reconciliation_cases", "ingested_documents"):
            conn.execute(f"DELETE FROM {table}")


# ----------------------------------------------------------------- documents


def _business_key(doc: PurchaseOrder | Invoice) -> str:
    number = doc.po_id if isinstance(doc, PurchaseOrder) else doc.invoice_id
    return f"{doc.buyer_id}|{doc.supplier_id}|{number}"


def _po_key_for(invoice: Invoice) -> str:
    return f"{invoice.buyer_id}|{invoice.supplier_id}|{invoice.po_references[0]}"


def _find_by_hash(conn: sqlite3.Connection, doc_type: str, content_hash: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ingested_documents WHERE doc_type = ? AND content_hash = ?"
        " AND validation_status = 'VALID' ORDER BY ingested_at LIMIT 1",
        (doc_type, content_hash),
    ).fetchone()


def _find_by_key(conn: sqlite3.Connection, doc_type: str, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ingested_documents WHERE doc_type = ? AND business_key = ?"
        " AND validation_status = 'VALID' ORDER BY ingested_at LIMIT 1",
        (doc_type, key),
    ).fetchone()


def _insert_document(
    conn: sqlite3.Connection,
    doc_type: str,
    data: bytes,
    business_key: str | None,
    partner: str | None,
    status: str,
    errors: str | None = None,
) -> str:
    doc_id = db.new_id(f"DOC{doc_type}")
    conn.execute(
        """INSERT INTO ingested_documents (id, doc_type, original_bytes, content_hash,
           business_key, ingested_at, tenant, trading_partner, parser_version,
           validation_status, validation_errors)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_id, doc_type, data, parser.content_hash(data), business_key, db.utc_now(),
         "default", partner, parser.PARSER_VERSION, status, errors),
    )
    return doc_id


def _insert_case(
    conn: sqlite3.Connection,
    status: str,
    po_id: str | None,
    invoice_id: str | None,
    result: dict[str, Any],
    reference_version: str | None = None,
    policy_version: str | None = None,
) -> str:
    case_id = db.new_id("CASE")
    now = db.utc_now()
    conn.execute(
        """INSERT INTO reconciliation_cases (id, po_id, invoice_id, status, review_status,
           mode, created_at, updated_at, evidence_version, version, reference_version,
           policy_version, result_json)
           VALUES (?, ?, ?, ?, 'OPEN', '2way', ?, ?, 1, 1, ?, ?, ?)""",
        (case_id, po_id, invoice_id, status, now, now, reference_version, policy_version,
         _dumps(result)),
    )
    return case_id


def _insert_findings(
    conn: sqlite3.Connection, case_id: str, evidence_version: int, result: CaseResult
) -> None:
    now = db.utc_now()
    rows = [(f, None, None) for f in result.header_findings]
    for line in result.line_results:
        rows.extend((f, line.invoice_line_id, line.po_line_id) for f in line.findings)
    for finding, inv_line, po_line in rows:
        conn.execute(
            """INSERT INTO case_findings (id, case_id, evidence_version, finding_type,
               check_name, check_status, invoice_line, po_line, po_value, invoice_value,
               normalized_po_value, normalized_invoice_value, delta, tolerance_applied,
               message, evidence_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{case_id}-v{evidence_version}-{finding.finding_id}", case_id, evidence_version,
             finding.result_state.value if finding.result_state else None, finding.check,
             finding.status.value, inv_line, po_line, finding.po_value, finding.invoice_value,
             finding.normalized_po_value, finding.normalized_invoice_value, finding.delta,
             finding.tolerance_applied, finding.message, _dumps(finding.evidence_ids), now),
        )


def _insert_allocations(
    conn: sqlite3.Connection,
    case_id: str,
    evidence_version: int,
    po_doc_id: str,
    invoice_doc_id: str,
    result: CaseResult,
) -> None:
    conn.execute("UPDATE allocations SET active = 0 WHERE case_id = ?", (case_id,))
    for alloc in result.allocations:
        conn.execute(
            """INSERT INTO allocations (id, case_id, evidence_version, po_id, po_line,
               invoice_id, quantity, uom, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (db.new_id("ALLOC"), case_id, evidence_version, po_doc_id, alloc["po_line"],
             invoice_doc_id, alloc["quantity"], alloc["uom"], db.utc_now()),
        )


# ----------------------------------------------------------------- ingestion


def _quarantine(
    conn: sqlite3.Connection, doc_type: str, data: bytes, err: ParseError, actor: str
) -> IngestOutcome:
    error = {"field": err.field_name, "message": err.message, "raw_value": repr(err.raw_value)}
    with db.transaction(conn):
        doc_id = _insert_document(conn, doc_type, data, None, None, "QUARANTINED", _dumps(error))
        case_id = _insert_case(
            conn, FindingStatus.QUARANTINED.value,
            doc_id if doc_type == "850" else None,
            doc_id if doc_type != "850" else None,
            {"overall_status": "QUARANTINED", "error": error},
        )
        _audit(conn, case_id=case_id, event_type="VALIDATION_FAILED", actor=actor,
               after_state="QUARANTINED", doc_hashes={doc_type: parser.content_hash(data)},
               calculations={"error": error, "parser_version": parser.PARSER_VERSION})
    return IngestOutcome(case_id, "QUARANTINED", "QUARANTINED",
                         f"{err.field_name} — {err.message}")


def _parse_or_quarantine(
    conn: sqlite3.Connection, src: Source, doc_type: str, actor: str
) -> tuple[PurchaseOrder | Invoice | None, bytes, IngestOutcome | None]:
    data = _bytes(src)
    try:
        doc = parser.parse_po(data) if doc_type == "850" else parser.parse_invoice(data)
    except ParseError as err:
        return None, data, _quarantine(conn, doc_type, data, err, actor)
    return doc, data, None


def _case_for_invoice(conn: sqlite3.Connection, invoice_doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reconciliation_cases WHERE invoice_id = ? ORDER BY created_at LIMIT 1",
        (invoice_doc_id,),
    ).fetchone()


def process(
    conn: sqlite3.Connection,
    invoice_src: Source,
    po_src: Source | None = None,
    receipt_src: Source | None = None,
    reference_dir: str | Path | None = None,
    policy: ReconciliationPolicy | None = None,
    actor: str = "analyst",
) -> IngestOutcome:
    """Ingest an invoice (plus optional PO and 861) and reconcile it two-way."""
    notes: list[str] = []

    if po_src is not None:
        parsed, po_bytes, bad = _parse_or_quarantine(conn, po_src, "850", actor)
        if bad:
            return bad
        po: PurchaseOrder = parsed  # type: ignore[assignment]
        if _find_by_hash(conn, "850", po.content_hash) is None:
            if (other := _find_by_key(conn, "850", _business_key(po))) is not None:
                return _duplicate_conflict(conn, "850", po_bytes, _business_key(po), other,
                                           actor)
            with db.transaction(conn):
                po_doc_id = _insert_document(conn, "850", po_bytes, _business_key(po),
                                             po.supplier_id, "VALID")
                _audit(conn, event_type="DOCUMENT_INGESTED", actor=actor, after_state="VALID",
                       doc_hashes={po_doc_id: po.content_hash},
                       calculations={"doc_type": "850",
                                     "parser_version": parser.PARSER_VERSION})

    parsed_inv, inv_bytes, bad = _parse_or_quarantine(conn, invoice_src, "810", actor)
    if bad:
        return bad
    invoice: Invoice = parsed_inv  # type: ignore[assignment]

    replay = _find_by_hash(conn, "810", invoice.content_hash)
    if replay is not None and (case := _case_for_invoice(conn, replay["id"])) is not None:
        with db.transaction(conn):
            _audit(conn, case_id=case["id"], event_type="REPLAY_NOOP", actor=actor,
                   before_state=case["status"], after_state=case["status"],
                   doc_hashes={replay["id"]: invoice.content_hash})
        return IngestOutcome(case["id"], "REPLAY_NOOP", case["status"],
                             "Identical invoice already processed; returning the original case.")
    inv_key = _business_key(invoice)
    if (other := _find_by_key(conn, "810", inv_key)) is not None:
        return _duplicate_conflict(conn, "810", inv_bytes, inv_key, other, actor)

    receipt = None
    if receipt_src is not None:
        receipt = parser.parse_receipt(_bytes(receipt_src))
        schema = "valid P2 ReceivingAdvice" if receipt.receipt else "not P2-schema valid"
        notes.append(f"861 receipt stored ({schema}) but unsupported for reconciliation "
                     "(THREE_WAY_ENABLED=false); receipt check NOT_EVALUATED.")

    po_row = _find_by_key(conn, "850", _po_key_for(invoice))
    return _reconcile_new(conn, invoice, inv_bytes, po_row, reference_dir, policy, actor,
                          notes, receipt_src, receipt.content_hash if receipt else None)


def _duplicate_conflict(
    conn: sqlite3.Connection,
    doc_type: str,
    data: bytes,
    key: str,
    original: sqlite3.Row,
    actor: str,
) -> IngestOutcome:
    original_case = _case_for_invoice(conn, original["id"]) if doc_type == "810" else None
    detail = {"business_key": key, "original_document": original["id"],
              "original_hash": original["content_hash"],
              "original_case": original_case["id"] if original_case else None}
    with db.transaction(conn):
        doc_id = _insert_document(conn, doc_type, data, key, None, "DUPLICATE_CONFLICT",
                                  _dumps(detail))
        case_id = _insert_case(
            conn, FindingStatus.DUPLICATE_CONFLICT.value, None,
            doc_id if doc_type == "810" else None,
            {"overall_status": "DUPLICATE_CONFLICT", "conflict": detail},
        )
        _audit(conn, case_id=case_id, event_type="DUPLICATE_CONFLICT", actor=actor,
               after_state="DUPLICATE_CONFLICT",
               doc_hashes={"new": parser.content_hash(data),
                           original["id"]: original["content_hash"]},
               calculations=detail)
    return IngestOutcome(case_id, "DUPLICATE_CONFLICT", "DUPLICATE_CONFLICT",
                         f"Same business key {key} with different content; held pending "
                         "verified revision.")


def _run_rules(
    po: PurchaseOrder,
    invoice: Invoice,
    po_doc_id: str,
    invoice_doc_id: str | None,
    conn: sqlite3.Connection,
    reference_dir: str | Path | None,
    policy: ReconciliationPolicy | None,
) -> tuple[CaseResult, str]:
    ref_dir = Path(reference_dir) if reference_dir else None
    uom_ref = reference.load_uom_conversions(ref_dir / reference.UOM_FILE if ref_dir else None)
    price_ref = reference.load_price_contracts(
        ref_dir / reference.CONTRACT_FILE if ref_dir else None
    )
    prior = get_prior_allocations(po_doc_id, [ln.line_id for ln in po.lines], conn,
                                  invoice_doc_id)
    result = comparator.compare(po, invoice, uom_ref, price_ref, policy, prior)
    return result, reference.reference_version(ref_dir)


def _evaluate(
    conn: sqlite3.Connection,
    invoice: Invoice,
    po_row: sqlite3.Row | None,
    invoice_doc_id: str | None,
    reference_dir: str | Path | None,
    policy: ReconciliationPolicy | None,
) -> tuple[str, dict[str, Any], CaseResult | None, str | None, list[str]]:
    """Return (state, result_dict, CaseResult|None, reference_version, retry_errors)."""
    if po_row is None:
        return (
            FindingStatus.WAIT_DOCUMENT.value,
            {"overall_status": "WAIT_DOCUMENT",
             "missing": f"PO {invoice.po_references[0]} for buyer {invoice.buyer_id} / "
             f"supplier {invoice.supplier_id} not found; no cross-partner linking."},
            None, None, [],
        )
    po = parser.parse_po(bytes(po_row["original_bytes"]))
    attempts = get_settings().max_retry_attempts
    errors: list[str] = []
    for _attempt in range(attempts):
        try:
            result, ref_version = _run_rules(po, invoice, po_row["id"], invoice_doc_id, conn,
                                             reference_dir, policy)
            return (result.overall_status.value, result_to_dict(result), result, ref_version,
                    errors)
        except Exception as exc:  # noqa: BLE001 - any rules/reference failure is a hold
            errors.append(f"{type(exc).__name__}: {exc}")
    return (
        FindingStatus.TECHNICAL_HOLD.value,
        {"overall_status": "TECHNICAL_HOLD", "errors": errors,
         "message": f"Rules engine failed after {attempts} attempts; no pass."},
        None, None, errors,
    )


def _reconcile_new(
    conn: sqlite3.Connection,
    invoice: Invoice,
    inv_bytes: bytes,
    po_row: sqlite3.Row | None,
    reference_dir: str | Path | None,
    policy: ReconciliationPolicy | None,
    actor: str,
    notes: list[str],
    receipt_src: Source | None,
    receipt_hash: str | None,
) -> IngestOutcome:
    state, result_dict, result, ref_version, errors = _evaluate(
        conn, invoice, po_row, None, reference_dir, policy
    )
    result_dict["notes"] = notes
    policy_version = result.policy_version if result else None
    with db.transaction(conn):
        inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                      invoice.supplier_id, "VALID")
        hashes = {inv_doc_id: invoice.content_hash}
        if po_row is not None:
            hashes[po_row["id"]] = po_row["content_hash"]
        if receipt_src is not None:
            receipt_id = _insert_document(conn, "861", _bytes(receipt_src), None,
                                          invoice.supplier_id, "STORED_UNSUPPORTED",
                                          "P2 three-way matching disabled; stored only.")
            hashes[receipt_id] = receipt_hash or ""
        case_id = _insert_case(conn, state, po_row["id"] if po_row else None, inv_doc_id,
                               result_dict, ref_version, policy_version)
        _audit(conn, case_id=case_id, event_type="DOCUMENT_INGESTED", actor=actor,
               after_state="VALID", doc_hashes=hashes,
               calculations={"parser_version": parser.PARSER_VERSION, "notes": notes})
        for err in errors:
            _audit(conn, case_id=case_id, event_type="RULES_RETRY", actor="rules-engine",
                   calculations={"error": err})
        if result is not None and po_row is not None:
            _insert_findings(conn, case_id, 1, result)
            _insert_allocations(conn, case_id, 1, po_row["id"], inv_doc_id, result)
        _audit(conn, case_id=case_id, event_type="RULES_EXECUTED", actor="rules-engine",
               after_state=state, doc_hashes=hashes, policy_version=policy_version,
               reference_version=ref_version,
               findings=_dumps(result_dict.get("not_evaluated_checks", [])),
               calculations={"run_id": result.run_id if result else None,
                             "allocations": result.allocations if result else []})
    return IngestOutcome(case_id, "CREATED", state, "; ".join(notes))


# --------------------------------------------------------------------- rerun


def rerun_case(
    conn: sqlite3.Connection,
    case_id: str,
    corrected_invoice_src: Source | None = None,
    reference_dir: str | Path | None = None,
    policy: ReconciliationPolicy | None = None,
    actor: str = "analyst",
) -> str:
    """Rerun checks on new evidence. Original bytes stay immutable; history is kept."""
    case = db.get_case(conn, case_id)
    if case is None or case["invoice_id"] is None:
        raise ReviewValidationError("case has no invoice to rerun")
    old_doc = db.get_document(conn, case["invoice_id"])
    inv_bytes = _bytes(corrected_invoice_src) if corrected_invoice_src else bytes(
        old_doc["original_bytes"]
    )
    invoice = parser.parse_invoice(inv_bytes)
    po_row = _find_by_key(conn, "850", _po_key_for(invoice))
    state, result_dict, result, ref_version, errors = _evaluate(
        conn, invoice, po_row, case["invoice_id"], reference_dir, policy
    )
    new_ev = case["evidence_version"] + 1
    with db.transaction(conn):
        inv_doc_id = case["invoice_id"]
        if corrected_invoice_src is not None:
            inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                          invoice.supplier_id, "VALID_REVISION",
                                          f"supersedes {case['invoice_id']}")
        conn.execute(
            """UPDATE reconciliation_cases SET status = ?, po_id = ?, invoice_id = ?,
               evidence_version = ?, version = version + 1, review_status = 'OPEN',
               disposition = NULL, updated_at = ?, reference_version = ?, policy_version = ?,
               result_json = ? WHERE id = ?""",
            (state, po_row["id"] if po_row else None, inv_doc_id, new_ev, db.utc_now(),
             ref_version, result.policy_version if result else None, _dumps(result_dict),
             case_id),
        )
        for err in errors:
            _audit(conn, case_id=case_id, event_type="RULES_RETRY", actor="rules-engine",
                   calculations={"error": err})
        if result is not None and po_row is not None:
            _insert_findings(conn, case_id, new_ev, result)
            _insert_allocations(conn, case_id, new_ev, po_row["id"], inv_doc_id, result)
        _audit(conn, case_id=case_id, event_type="RERUN_VERSION_SUPERSEDED", actor=actor,
               before_state=case["status"], after_state=state,
               doc_hashes={inv_doc_id: invoice.content_hash}, reference_version=ref_version,
               policy_version=result.policy_version if result else None,
               calculations={"evidence_version": new_ev,
                             "previous_evidence_version": case["evidence_version"],
                             "stale_recommendations_invalidated": True})
    return state


# ------------------------------------------------------------------------ AI


def get_cached_explanation(
    conn: sqlite3.Connection, case_id: str, evidence_version: int
) -> tuple[AIExplanation, str] | None:
    row = conn.execute(
        "SELECT * FROM ai_explanations WHERE case_id = ? AND evidence_version = ?",
        (case_id, evidence_version),
    ).fetchone()
    if row is None:
        return None
    return AIExplanation.model_validate_json(row["explanation_json"]), row[
        "recommendation_version"
    ]


def request_ai_explanation(
    conn: sqlite3.Connection,
    case_id: str,
    provider: str | None = None,
    generate: ai_resolver.Generate | None = None,
    timeout_seconds: float | None = None,
    actor: str = "analyst",
) -> tuple[AIExplanation, str | None]:
    """On-demand, cached per evidence version. AI_UNAVAILABLE results are not cached."""
    case = db.get_case(conn, case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    ev = case["evidence_version"]
    if (cached := get_cached_explanation(conn, case_id, ev)) is not None:
        return cached
    bundle = json.loads(case["result_json"]).get("ai_bundle")
    if bundle is None:
        expl = AIExplanation(
            status="AI_ABSTAIN", finding_ids=[], possible_cause="", cited_evidence_ids=[],
            uncertainty_note="No deterministic findings exist for this case; the AI has "
            "nothing to explain.", missing_evidence=[], suggested_next_action="MANUAL_REVIEW",
            model_version="none", prompt_version=ai_resolver.PROMPT_VERSION,
        )
    else:
        expl = ai_resolver.explain(bundle, provider=provider, generate=generate,
                                   timeout_seconds=timeout_seconds)
    digest = hashlib.sha256(expl.model_dump_json().encode()).hexdigest()[:8]
    rec_version = f"{case_id}-ev{ev}-{digest}"
    with db.transaction(conn):
        if expl.status != "AI_UNAVAILABLE":
            conn.execute(
                """INSERT INTO ai_explanations (id, case_id, evidence_version,
                   recommendation_version, status, explanation_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (db.new_id("AI"), case_id, ev, rec_version, expl.status,
                 expl.model_dump_json(), db.utc_now()),
            )
        _audit(conn, case_id=case_id,
               event_type="AI_RESULT" if expl.status == "OK" else expl.status,
               actor="ai-resolver", before_state=case["status"], after_state=case["status"],
               recommendation_version=rec_version, source_pointers=expl.cited_evidence_ids,
               calculations={"model_version": expl.model_version,
                             "prompt_version": expl.prompt_version})
    return expl, (rec_version if expl.status != "AI_UNAVAILABLE" else None)


# -------------------------------------------------------------------- review


def review_action(
    conn: sqlite3.Connection, request: ReviewRequest, actor: str = "reviewer"
) -> sqlite3.Row:
    """Record approve / reject / escalate atomically with its audit event and record.

    ReviewRequest already guarantees a non-empty reason and an owner for ESCALATE.
    Raises ReviewValidationError, StaleVersionError or AuditPersistenceError; on any
    error nothing is committed.
    """
    key = request.idempotency_key or uuid.uuid4().hex
    if db.audit_key_exists(conn, key):
        return db.get_case(conn, request.case_id)  # retry of an already-committed action
    action = request.action
    owner = (request.escalation_owner or "").strip()
    if action == ReviewActionType.ESCALATE and owner.lower() not in ESCALATION_OWNERS:
        raise ReviewValidationError(
            f"Escalation requires an accountable owner: {', '.join(ESCALATION_OWNERS)}."
        )
    case = db.get_case(conn, request.case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    if case["version"] != request.case_version:
        raise StaleVersionError(STALE_CASE)
    if request.recommendation_version is not None:
        current = get_cached_explanation(conn, request.case_id, case["evidence_version"])
        if current is None or current[1] != request.recommendation_version:
            raise StaleVersionError(
                "The AI recommendation is stale for the current evidence version."
            )
    if (action == ReviewActionType.APPROVE and request.close
            and FindingStatus(case["status"]) in BLOCKING_STATES):
        raise ReviewValidationError(
            f"Closure blocked: case is {case['status']}; required evidence is missing or "
            "unresolved. Approve a next step instead."
        )

    new_status = REVIEW_OUTCOMES[action]
    disposition = {
        ReviewActionType.APPROVE: "APPROVED_EXCEPTION_CLOSED" if request.close
        else "NEXT_STEP_APPROVED",
        ReviewActionType.REJECT: None,
        ReviewActionType.ESCALATE: "PENDING_OWNER",
    }[action]
    reason = request.reason.strip()

    with db.transaction(conn):
        cur = conn.execute(
            """UPDATE reconciliation_cases SET review_status = ?, disposition = ?,
               version = version + 1, updated_at = ? WHERE id = ? AND version = ?""",
            (new_status.value, disposition, db.utc_now(), request.case_id,
             request.case_version),
        )
        if cur.rowcount != 1:
            raise StaleVersionError(STALE_CASE)
        event_id = _audit(
            conn, case_id=request.case_id, event_type=f"HUMAN_{action.value}", actor=actor,
            correlation_key=key,
            before_state=f"{case['status']}/{case['review_status']}",
            after_state=f"{case['status']}/{new_status.value}",
            recommendation_version=request.recommendation_version, reason=reason,
            escalation_owner=owner if action == ReviewActionType.ESCALATE else None,
            policy_version=case["policy_version"],
            calculations={"disposition": disposition,
                          "evidence_version": case["evidence_version"],
                          "case_version": request.case_version + 1},
        )
        db.write_review_record(conn, ReviewRecord(
            review_id=db.new_id("REV"), case_id=request.case_id,
            case_version=request.case_version, action=action, reason=reason,
            escalation_owner=owner or None, utc_timestamp=datetime.now(timezone.utc),
            audit_event_id=event_id,
        ))
    return db.get_case(conn, request.case_id)


def case_detail(conn: sqlite3.Connection, case_id: str) -> dict[str, Any]:
    case = db.get_case(conn, case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    cached = get_cached_explanation(conn, case_id, case["evidence_version"])
    return {
        "case": dict(case),
        "result": json.loads(case["result_json"] or "{}"),
        "findings": [dict(r) for r in db.list_findings(conn, case_id)],
        "explanation": cached[0] if cached else None,
        "recommendation_version": cached[1] if cached else None,
        "audit": [dict(r) for r in db.list_audit_events(conn, case_id)],
        "reviews": [dict(r) for r in db.list_review_records(conn, case_id)],
    }

