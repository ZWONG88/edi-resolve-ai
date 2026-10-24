"""Orchestrator: ingest, reconcile, explain, review, rerun, import datasets, remit.

The single backend entry point for the Streamlit frontend (PR #1 backend plan).
Pages import only this module. Every state change and its audit event are committed
in one transaction.

With THREE_WAY_ENABLED (default) invoices are matched against the PO and the accepted
receipt quantity from the 861s (team rules doc). Clean invoices are auto-approved,
discrepancies get a short-pay suggestion, and an EDI 820 file is generated (never
transmitted) when the disposition allows payment.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend import adapters, ai_resolver, comparator, db, parser, reference, remittance
from backend import disposition as dispo
from backend.allocation import get_prior_allocations
from backend.config import get_settings
from backend.exceptions import ParseError, ReviewValidationError, StaleVersionError
from backend.models import (
    BLOCKING_STATES,
    AIExplanation,
    AuditEvent,
    CaseResult,
    Disposition,
    FindingStatus,
    Invoice,
    Money,
    PurchaseOrder,
    ReconciliationPolicy,
    ReviewActionType,
    ReviewRecord,
    ReviewRequest,
    ReviewStatus,
)
from backend.money import fmt_money
from backend.receipts import ReceiptLedger, build_ledger

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
AUTO_APPROVE = {Disposition.AUTO_APPROVED, Disposition.APPROVED_WITH_SHORT_PAY}


class IngestOutcome:
    """Result of process(): which case, and what happened to the upload."""

    def __init__(self, case_id: str, outcome: str, result_state: str, message: str = "",
                 disposition: str | None = None) -> None:
        self.case_id = case_id
        self.outcome = outcome  # CREATED, REPLAY_NOOP, QUARANTINED, DUPLICATE_CONFLICT
        self.result_state = result_state
        self.message = message
        self.disposition = disposition


def _bytes(src: Source) -> bytes:
    return parser.normalize_source(src if isinstance(src, bytes) else Path(src).read_bytes())


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def _policy(policy: ReconciliationPolicy | None) -> ReconciliationPolicy:
    if policy is not None:
        return policy
    settings = get_settings()
    return ReconciliationPolicy.baseline().model_copy(update={
        "de_minimis_line_tolerance": Money(amount=settings.de_minimis_line_tolerance),
        "high_value_hold_threshold": Money(amount=settings.high_value_hold_threshold),
        "three_way_enabled": settings.three_way_enabled,
    })


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


def current_remittance(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM remittances WHERE case_id = ? AND status = ? ORDER BY created_at DESC"
        " LIMIT 1", (case_id, remittance.STATUS),
    ).fetchone()


def dataset_folders(root: str | Path) -> list[Path]:
    """Sub-folders of `root` holding team-schema JSON documents."""
    return [folder for folder in sorted(Path(root).iterdir())
            if folder.is_dir() and any(_is_team_file(f) for f in folder.glob("*.json"))]


def reset_test_data(conn: sqlite3.Connection, actor: str = "analyst") -> dict[str, int]:
    """Delete test/sample data (sandbox=1) only; real cases and their history are kept.

    A sandbox document that a real case still depends on (its PO or invoice, or an 861
    for that PO) is kept. The reset itself is recorded as a TEST_DATA_RESET audit event.
    """
    cases = [r["id"] for r in conn.execute(
        "SELECT id FROM reconciliation_cases WHERE sandbox = 1")]
    kept = {r[0] for r in conn.execute(
        """SELECT d.id FROM ingested_documents d JOIN reconciliation_cases c
           ON c.sandbox = 0 AND d.id IN (c.po_id, c.invoice_id)
           UNION SELECT r.id FROM ingested_documents r JOIN ingested_documents p
           ON r.doc_type = '861' AND r.business_key = p.business_key
           JOIN reconciliation_cases c ON c.sandbox = 0 AND c.po_id = p.id""")}
    sandbox_docs = [r["id"] for r in conn.execute(
        "SELECT id FROM ingested_documents WHERE sandbox = 1")]
    docs = [d for d in sandbox_docs if d not in kept]
    gone = set(docs)
    orphan_events = []
    for row in conn.execute("SELECT id, doc_hashes FROM audit_events WHERE case_id IS NULL"):
        keys = set(json.loads(row["doc_hashes"] or "{}"))
        if keys and keys <= gone:
            orphan_events.append(row["id"])
    files = [Path(r["file_path"]) for r in conn.execute(
        "SELECT r.file_path FROM remittances r JOIN reconciliation_cases c"
        " ON c.id = r.case_id WHERE c.sandbox = 1") if r["file_path"]]

    def delete(table: str, column: str, ids: list[str]) -> int:
        total = 0
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            total += conn.execute(f"DELETE FROM {table} WHERE {column} IN "
                                  f"({','.join('?' * len(chunk))})", chunk).rowcount
        return total

    with db.transaction(conn):
        for table in ("review_records", "ai_explanations", "remittances",
                      "receipt_allocations", "allocations", "case_findings"):
            delete(table, "case_id", cases)
        events = delete("audit_events", "case_id", cases)
        events += delete("audit_events", "id", orphan_events)
        counts = {"cases": delete("reconciliation_cases", "id", cases),
                  "documents": delete("ingested_documents", "id", docs),
                  "audit_events": events, "documents_kept": len(sandbox_docs) - len(docs)}
        _audit(conn, event_type="TEST_DATA_RESET", actor=actor, calculations=counts,
               reason="Cleared test/sample data; real cases and their history kept.")
    for path in files:
        path.unlink(missing_ok=True)
    return counts


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
    sandbox: bool = False,
) -> str:
    doc_id = db.new_id(f"DOC{doc_type}")
    conn.execute(
        """INSERT INTO ingested_documents (id, doc_type, original_bytes, content_hash,
           business_key, ingested_at, tenant, trading_partner, parser_version,
           validation_status, validation_errors, sandbox)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (doc_id, doc_type, data, parser.content_hash(data), business_key, db.utc_now(),
         "default", partner, parser.PARSER_VERSION, status, errors, int(sandbox)),
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
    mode: str = "2way",
    sandbox: bool = False,
) -> str:
    case_id = db.new_id("CASE")
    now = db.utc_now()
    disposition = dispo.classify(FindingStatus(status), mode, None, Decimal("0"))
    conn.execute(
        """INSERT INTO reconciliation_cases (id, po_id, invoice_id, status, review_status,
           mode, created_at, updated_at, evidence_version, version, reference_version,
           policy_version, result_json, disposition_status, sandbox)
           VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)""",
        (case_id, po_id, invoice_id, status, mode, now, now, reference_version,
         policy_version, _dumps(result), disposition.value, int(sandbox)),
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
    for table, rows in (("allocations", result.allocations),
                        ("receipt_allocations", result.receipt_allocations)):
        conn.execute(f"UPDATE {table} SET active = 0 WHERE case_id = ?", (case_id,))
        for alloc in rows:
            conn.execute(
                f"""INSERT INTO {table} (id, case_id, evidence_version, po_id, po_line,
                   invoice_id, quantity, uom, active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (db.new_id("ALLOC"), case_id, evidence_version, po_doc_id, alloc["po_line"],
                 invoice_doc_id, alloc["quantity"], alloc["uom"], db.utc_now()),
            )


def _apply_outcome(
    conn: sqlite3.Connection,
    case_id: str,
    evidence_version: int,
    result: CaseResult,
    invoice: Invoice,
) -> tuple[dict[str, Any], Path] | None:
    """Persist disposition and money; auto-approve; prepare the 820. In a transaction.

    Returns the 820 payload and its folder, to write after commit, if one applies.
    """
    def money(value: Money | None) -> str | None:
        return str(value.amount) if value is not None else None

    conn.execute(
        """UPDATE reconciliation_cases SET disposition_status = ?, invoiced_amount = ?,
           approved_amount = ?, blocked_amount = ? WHERE id = ?""",
        (result.disposition.value, money(result.invoiced_total), money(result.approved_total),
         money(result.blocked_total), case_id),
    )
    if result.disposition in AUTO_APPROVE:
        conn.execute("UPDATE reconciliation_cases SET review_status = ? WHERE id = ?",
                     (ReviewStatus.AUTO_APPROVED.value, case_id))
        _audit(conn, case_id=case_id, event_type="AUTO_APPROVED", actor="rules-engine",
               before_state="OPEN", after_state=ReviewStatus.AUTO_APPROVED.value,
               reason=f"Rule 5.1: {result.disposition.value}; blocked cash "
               f"${result.blocked_total} of ${result.invoiced_total}.",
               policy_version=result.policy_version,
               calculations={"disposition": result.disposition.value,
                             "approved": money(result.approved_total),
                             "blocked": money(result.blocked_total)})
    if remittance.can_generate(result):
        return _prepare_820(conn, case_id, evidence_version, result, invoice)
    return None


def _prepare_820(
    conn: sqlite3.Connection,
    case_id: str,
    evidence_version: int,
    result: CaseResult,
    invoice: Invoice,
) -> tuple[dict[str, Any], Path]:
    payload = remittance.build_820(result, invoice, case_id, evidence_version)
    directory = Path(get_settings().remittance_dir)
    path = remittance.target_path(payload, directory)
    conn.execute("UPDATE remittances SET status = 'SUPERSEDED' WHERE case_id = ?", (case_id,))
    conn.execute(
        """INSERT INTO remittances (id, case_id, evidence_version, invoice_number, disposition,
           paid_amount, status, file_path, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (db.new_id("REM"), case_id, evidence_version, invoice.invoice_id,
         payload["disposition"], payload["paid_amount"], remittance.STATUS, str(path),
         _dumps(payload), db.utc_now()),
    )
    _audit(conn, case_id=case_id, event_type="REMITTANCE_820_GENERATED", actor="rules-engine",
           calculations={"paid_amount": payload["paid_amount"],
                         "adjustment_amount": payload["adjustment_amount"],
                         "file": str(path), "transmitted": False})
    return payload, directory


def _write_820(prepared: tuple[dict[str, Any], Path] | None) -> None:
    if prepared is not None:
        remittance.write_820(*prepared)


# ----------------------------------------------------------------- ingestion


def _quarantine(
    conn: sqlite3.Connection, doc_type: str, data: bytes, err: ParseError, actor: str,
    sandbox: bool = False,
) -> IngestOutcome:
    error = {"field": err.field_name, "message": err.message, "raw_value": repr(err.raw_value)}
    with db.transaction(conn):
        doc_id = _insert_document(conn, doc_type, data, None, None, "QUARANTINED", _dumps(error),
                                  sandbox)
        case_id = _insert_case(
            conn, FindingStatus.QUARANTINED.value,
            doc_id if doc_type == "850" else None,
            doc_id if doc_type != "850" else None,
            {"overall_status": "QUARANTINED", "error": error}, sandbox=sandbox,
        )
        _audit(conn, case_id=case_id, event_type="VALIDATION_FAILED", actor=actor,
               after_state="QUARANTINED", doc_hashes={doc_type: parser.content_hash(data)},
               calculations={"error": error, "parser_version": parser.PARSER_VERSION})
    return IngestOutcome(case_id, "QUARANTINED", "QUARANTINED",
                         f"{err.field_name} — {err.message}",
                         Disposition.SCHEMA_VALIDATION_ERROR.value)


def _case_for_invoice(conn: sqlite3.Connection, invoice_doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM reconciliation_cases WHERE invoice_id = ? ORDER BY created_at LIMIT 1",
        (invoice_doc_id,),
    ).fetchone()


def _ingest_po(conn: sqlite3.Connection, data: bytes, actor: str,
               sandbox: bool = False) -> IngestOutcome | None:
    """Store a PO. Returns an outcome only when it becomes its own case (an error)."""
    try:
        po = parser.parse_po(data)
    except ParseError as err:
        return _quarantine(conn, "850", data, err, actor, sandbox)
    if _find_by_hash(conn, "850", po.content_hash) is not None:
        return None  # replay of a stored PO
    if (other := _find_by_key(conn, "850", _business_key(po))) is not None:
        return _duplicate_conflict(conn, "850", data, _business_key(po), other, actor, sandbox)
    with db.transaction(conn):
        po_doc_id = _insert_document(conn, "850", data, _business_key(po), po.supplier_id,
                                     "VALID", sandbox=sandbox)
        _audit(conn, event_type="DOCUMENT_INGESTED", actor=actor, after_state="VALID",
               doc_hashes={po_doc_id: po.content_hash},
               calculations={"doc_type": "850", "parser_version": parser.PARSER_VERSION})
    return None


def ingest_receipt(conn: sqlite3.Connection, src: Source, actor: str = "analyst",
                   sandbox: bool = False) -> str:
    """Store an 861 for 3-way matching and rerun invoices waiting on it. Returns a note."""
    data = _bytes(src)
    try:
        stored = parser.parse_receipt(data)
    except ParseError as err:
        with db.transaction(conn):
            _insert_document(conn, "861", data, None, None, "QUARANTINED",
                             _dumps({"field": err.field_name, "message": err.message}),
                             sandbox)
        return f"861 rejected: {err.field_name} — {err.message}"
    receipt = stored.receipt
    if _find_by_hash(conn, "861", stored.content_hash) is not None:
        return "861 already stored (replay ignored)."
    if receipt is None or not (receipt.buyer_id and receipt.supplier_id):
        with db.transaction(conn):
            _insert_document(conn, "861", data, None, None, "UNSUPPORTED_PROFILE",
                             "861 does not fit the supported receipt profile", sandbox)
        return "861 stored but not usable: it does not fit the supported receipt profile."
    key = f"{receipt.buyer_id}|{receipt.supplier_id}|{receipt.po_references[0]}"
    with db.transaction(conn):
        doc_id = _insert_document(conn, "861", data, key, receipt.supplier_id, "VALID",
                                  sandbox=sandbox)
        _audit(conn, event_type="RECEIPT_INGESTED", actor=actor, after_state="VALID",
               doc_hashes={doc_id: stored.content_hash},
               calculations={"receipt_id": receipt.receipt_id, "po": key})
    reran = _rerun_waiting(conn, key, actor)
    return f"861 {receipt.receipt_id} stored" + (
        f"; reran {reran} case(s) waiting for it." if reran else ".")


def _rerun_waiting(conn: sqlite3.Connection, po_key: str, actor: str) -> int:
    rows = conn.execute(
        """SELECT c.id FROM reconciliation_cases c JOIN ingested_documents d ON d.id = c.po_id
           WHERE c.status = ? AND d.business_key = ?""",
        (FindingStatus.WAIT_RECEIPT.value, po_key),
    ).fetchall()
    for row in rows:
        rerun_case(conn, row["id"], actor=actor)
    return len(rows)


def process(
    conn: sqlite3.Connection,
    invoice_src: Source,
    po_src: Source | None = None,
    receipt_src: Source | None = None,
    reference_dir: str | Path | None = None,
    policy: ReconciliationPolicy | None = None,
    actor: str = "analyst",
    sandbox: bool = False,
) -> IngestOutcome:
    """Ingest an invoice (plus optional PO and 861) and reconcile it.

    sandbox marks test/sample data that reset_test_data() may delete; uploads of real
    documents leave it False and are never deleted.
    """
    notes: list[str] = []
    if po_src is not None:
        if (bad := _ingest_po(conn, _bytes(po_src), actor, sandbox)) is not None:
            return bad
    unsupported_receipt = None
    if receipt_src is not None:
        if get_settings().three_way_enabled:
            notes.append(ingest_receipt(conn, receipt_src, actor, sandbox))
        else:
            unsupported_receipt = _bytes(receipt_src)
            notes.append("861 receipt stored but unsupported for reconciliation "
                         "(THREE_WAY_ENABLED=false); receipt check NOT_EVALUATED.")
    return _ingest_invoice(conn, _bytes(invoice_src), reference_dir, policy, actor, notes,
                           unsupported_receipt, sandbox)


def _ingest_invoice(
    conn: sqlite3.Connection,
    inv_bytes: bytes,
    reference_dir: str | Path | None,
    policy: ReconciliationPolicy | None,
    actor: str,
    notes: list[str],
    unsupported_receipt: bytes | None = None,
    sandbox: bool = False,
) -> IngestOutcome:
    try:
        invoice = parser.parse_invoice(inv_bytes)
    except ParseError as err:
        return _quarantine(conn, "810", inv_bytes, err, actor, sandbox)

    replay = _find_by_hash(conn, "810", invoice.content_hash)
    if replay is not None and (case := _case_for_invoice(conn, replay["id"])) is not None:
        with db.transaction(conn):
            _audit(conn, case_id=case["id"], event_type="REPLAY_NOOP", actor=actor,
                   before_state=case["status"], after_state=case["status"],
                   doc_hashes={replay["id"]: invoice.content_hash})
        return IngestOutcome(case["id"], "REPLAY_NOOP", case["status"],
                             "Identical invoice already processed; returning the original case.",
                             case["disposition_status"])
    inv_key = _business_key(invoice)
    if (other := _find_by_key(conn, "810", inv_key)) is not None:
        return _duplicate_conflict(conn, "810", inv_bytes, inv_key, other, actor, sandbox)

    po_row = _find_by_key(conn, "850", _po_key_for(invoice))
    return _reconcile_new(conn, invoice, inv_bytes, po_row, reference_dir, policy, actor,
                          notes, unsupported_receipt, sandbox)


def _duplicate_conflict(
    conn: sqlite3.Connection,
    doc_type: str,
    data: bytes,
    key: str,
    original: sqlite3.Row,
    actor: str,
    sandbox: bool = False,
) -> IngestOutcome:
    original_case = _case_for_invoice(conn, original["id"]) if doc_type == "810" else None
    detail = {"business_key": key, "original_document": original["id"],
              "original_hash": original["content_hash"],
              "original_case": original_case["id"] if original_case else None}
    with db.transaction(conn):
        doc_id = _insert_document(conn, doc_type, data, key, None, "DUPLICATE_CONFLICT",
                                  _dumps(detail), sandbox)
        case_id = _insert_case(
            conn, FindingStatus.DUPLICATE_CONFLICT.value, None,
            doc_id if doc_type == "810" else None,
            {"overall_status": "DUPLICATE_CONFLICT", "conflict": detail}, sandbox=sandbox,
        )
        _audit(conn, case_id=case_id, event_type="DUPLICATE_CONFLICT", actor=actor,
               after_state="DUPLICATE_CONFLICT",
               doc_hashes={"new": parser.content_hash(data),
                           original["id"]: original["content_hash"]},
               calculations=detail)
    return IngestOutcome(case_id, "DUPLICATE_CONFLICT", "DUPLICATE_CONFLICT",
                         f"Same business key {key} with different content; held pending "
                         "verified revision.", Disposition.DUPLICATE_INVOICE_REJECTED.value)


def _receipt_ledger(conn: sqlite3.Connection, po_key: str) -> ReceiptLedger:
    rows = conn.execute(
        "SELECT original_bytes FROM ingested_documents WHERE doc_type = '861'"
        " AND business_key = ? AND validation_status = 'VALID'", (po_key,),
    ).fetchall()
    receipts = [parser.parse_receipt(bytes(r["original_bytes"])).receipt for r in rows]
    return build_ledger([r for r in receipts if r is not None])


def _run_rules(
    po: PurchaseOrder,
    invoice: Invoice,
    po_doc_id: str,
    invoice_doc_id: str | None,
    conn: sqlite3.Connection,
    reference_dir: str | Path | None,
    policy: ReconciliationPolicy,
) -> tuple[CaseResult, str]:
    ref_dir = Path(reference_dir) if reference_dir else None
    uom_ref = reference.load_uom_conversions(ref_dir / reference.UOM_FILE if ref_dir else None)
    price_ref = reference.load_price_contracts(
        ref_dir / reference.CONTRACT_FILE if ref_dir else None
    )
    line_ids = [ln.line_id for ln in po.lines]
    prior = get_prior_allocations(po_doc_id, line_ids, conn, invoice_doc_id)
    three_way = get_settings().three_way_enabled
    ledger = prior_receipt = None
    if three_way:
        ledger = _receipt_ledger(conn, _po_key_for(invoice))
        prior_receipt = get_prior_allocations(po_doc_id, line_ids, conn, invoice_doc_id,
                                              kind="receipt")
    result = comparator.compare(po, invoice, uom_ref, price_ref, policy, prior,
                                receipts=ledger, three_way=three_way,
                                prior_receipt_allocated=prior_receipt)
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
             "disposition": Disposition.PO_HEADER_MISMATCH.value,
             "missing": f"PO {invoice.po_references[0]} for buyer {invoice.buyer_id} / "
             f"supplier {invoice.supplier_id} not found; no cross-partner linking."},
            None, None, [],
        )
    po = parser.parse_po(bytes(po_row["original_bytes"]))
    policy = _policy(policy)
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
        {"overall_status": "TECHNICAL_HOLD", "disposition": Disposition.HOLD.value,
         "errors": errors,
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
    unsupported_receipt: bytes | None,
    sandbox: bool = False,
) -> IngestOutcome:
    state, result_dict, result, ref_version, errors = _evaluate(
        conn, invoice, po_row, None, reference_dir, policy
    )
    result_dict["notes"] = notes
    policy_version = result.policy_version if result else None
    prepared = None
    with db.transaction(conn):
        inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                      invoice.supplier_id, "VALID", sandbox=sandbox)
        hashes = {inv_doc_id: invoice.content_hash}
        if po_row is not None:
            hashes[po_row["id"]] = po_row["content_hash"]
        if unsupported_receipt is not None:
            receipt_id = _insert_document(conn, "861", unsupported_receipt, None,
                                          invoice.supplier_id, "STORED_UNSUPPORTED",
                                          "Three-way matching disabled; stored only.",
                                          sandbox)
            hashes[receipt_id] = parser.content_hash(unsupported_receipt)
        case_id = _insert_case(conn, state, po_row["id"] if po_row else None, inv_doc_id,
                               result_dict, ref_version, policy_version,
                               result.mode if result else "2way", sandbox)
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
                             "mode": result.mode if result else None,
                             "disposition": result_dict.get("disposition"),
                             "allocations": result.allocations if result else [],
                             "receipt_allocations": result.receipt_allocations
                             if result else []})
        if result is not None:
            prepared = _apply_outcome(conn, case_id, 1, result, invoice)
    _write_820(prepared)
    return IngestOutcome(case_id, "CREATED", state, "; ".join(notes),
                         result_dict.get("disposition"))


# ------------------------------------------------------------- dataset import


def _is_team_file(path: Path) -> bool:
    try:
        obj = adapters.loads(path.read_bytes())
    except (ParseError, OSError):
        return False
    docs = obj if isinstance(obj, list) else [obj]
    return bool(docs) and all(adapters.is_team_document(d) for d in docs)


def import_dataset(
    conn: sqlite3.Connection,
    folder: str | Path,
    reference_dir: str | Path | None = None,
    actor: str = "analyst",
    sandbox: bool = False,
) -> dict[str, Any]:
    """Import every 850/861/810 in a folder (POs and receipts first) and reconcile."""
    docs: dict[str, list[bytes]] = {"850": [], "861": [], "810": []}
    skipped: list[str] = []
    for path in sorted(Path(folder).glob("*.json")):
        try:
            for doc in adapters.load_team_file(path):
                kind = str(doc["transaction_type"])
                if kind in docs:
                    docs[kind].append(adapters.canonical_bytes(doc))
                else:
                    skipped.append(f"{path.name}: unsupported transaction {kind}")
        except ParseError as err:
            skipped.append(f"{path.name}: {err}")
    outcomes: list[IngestOutcome] = []
    for data in docs["850"]:
        if (bad := _ingest_po(conn, data, actor, sandbox)) is not None:
            outcomes.append(bad)
    receipt_notes = [ingest_receipt(conn, data, actor, sandbox) for data in docs["861"]]
    for data in docs["810"]:
        outcomes.append(_ingest_invoice(conn, data, reference_dir, None, actor, [], None,
                                        sandbox))
    return summarize(conn, outcomes, counts={k: len(v) for k, v in docs.items()},
                     skipped=skipped, receipt_notes=receipt_notes)


def summarize(conn: sqlite3.Connection, outcomes: list[IngestOutcome],
              **extra: Any) -> dict[str, Any]:
    """Per-case rows, counts by disposition and money totals for a batch."""
    rows = []
    totals = {"invoiced": Decimal("0"), "approved": Decimal("0"), "blocked": Decimal("0"),
              "held": Decimal("0")}
    by_disposition: dict[str, int] = {}
    for out in outcomes:
        case = db.get_case(conn, out.case_id)
        rows.append({"case_id": out.case_id, "outcome": out.outcome, "state": case["status"],
                     "disposition": case["disposition_status"],
                     "invoiced": case["invoiced_amount"], "approved": case["approved_amount"],
                     "blocked": case["blocked_amount"]})
        by_disposition[case["disposition_status"]] = (
            by_disposition.get(case["disposition_status"], 0) + 1)
        for key in ("invoiced", "approved", "blocked"):
            if case[f"{key}_amount"] is not None:
                totals[key] += Decimal(case[f"{key}_amount"])
        if case["approved_amount"] is None and case["invoiced_amount"] is not None:
            totals["held"] += Decimal(case["invoiced_amount"])
    return {"cases": rows, "by_disposition": by_disposition,
            "totals": {k: fmt_money(v) for k, v in totals.items()}, **extra}


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
    if case["status"] == FindingStatus.QUARANTINED.value:
        raise ReviewValidationError("Quarantined document cannot be rerun; upload a corrected "
                                    "file as a new document.")
    if case["status"] == FindingStatus.DUPLICATE_CONFLICT.value:
        raise ReviewValidationError("Duplicate conflict: rerun the original case with the "
                                    "corrected invoice instead.")
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
    prepared = None
    with db.transaction(conn):
        inv_doc_id = case["invoice_id"]
        if corrected_invoice_src is not None:
            inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                          invoice.supplier_id, "VALID_REVISION",
                                          f"supersedes {case['invoice_id']}",
                                          bool(case["sandbox"]))
        disposition = result.disposition.value if result else result_dict.get("disposition")
        conn.execute(
            """UPDATE reconciliation_cases SET status = ?, po_id = ?, invoice_id = ?,
               evidence_version = ?, version = version + 1, review_status = 'OPEN',
               disposition = NULL, updated_at = ?, reference_version = ?, policy_version = ?,
               result_json = ?, mode = ?, disposition_status = ?, invoiced_amount = NULL,
               approved_amount = NULL, blocked_amount = NULL WHERE id = ?""",
            (state, po_row["id"] if po_row else None, inv_doc_id, new_ev, db.utc_now(),
             ref_version, result.policy_version if result else None, _dumps(result_dict),
             result.mode if result else case["mode"], disposition, case_id),
        )
        conn.execute("UPDATE remittances SET status = 'SUPERSEDED' WHERE case_id = ?",
                     (case_id,))
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
                             "disposition": disposition,
                             "stale_recommendations_invalidated": True})
        if result is not None:
            prepared = _apply_outcome(conn, case_id, new_ev, result, invoice)
    _write_820(prepared)
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
    Rejecting or escalating voids a live 820; approving a payable invoice with no live
    820 (NEEDS_HUMAN_AP_REVIEW, or one rejected earlier) generates it.
    Raises ReviewValidationError, StaleVersionError or AuditPersistenceError; on any
    error nothing is committed.
    """
    key = request.idempotency_key or uuid.uuid4().hex
    action = request.action
    if (prior := db.get_audit_event_by_key(conn, key)) is not None:
        if (prior["case_id"], prior["event_type"]) != (request.case_id,
                                                       f"HUMAN_{action.value}"):
            raise ReviewValidationError("Idempotency key already used for a different action.")
        return db.get_case(conn, request.case_id)  # retry of an already-committed action
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

    prepared = None
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
        live = current_remittance(conn, request.case_id)
        if action != ReviewActionType.APPROVE and live is not None:
            conn.execute("UPDATE remittances SET status = 'SUPERSEDED' WHERE id = ?",
                         (live["id"],))
            _audit(conn, case_id=request.case_id, event_type="REMITTANCE_820_VOIDED",
                   actor=actor, reason=reason,
                   calculations={"remittance_id": live["id"], "file": live["file_path"],
                                 "transmitted": False})
        payable = {d.value for d in remittance.PAYABLE}
        if (action == ReviewActionType.APPROVE and live is None
                and case["disposition_status"] in payable):
            result = CaseResult.model_validate(json.loads(case["result_json"]))
            doc = db.get_document(conn, case["invoice_id"])
            invoice = parser.parse_invoice(bytes(doc["original_bytes"]))
            if remittance.can_generate(result, human_approved=True):
                prepared = _prepare_820(conn, request.case_id, case["evidence_version"],
                                        result, invoice)
    _write_820(prepared)
    return db.get_case(conn, request.case_id)


def case_detail(conn: sqlite3.Connection, case_id: str) -> dict[str, Any]:
    case = db.get_case(conn, case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    cached = get_cached_explanation(conn, case_id, case["evidence_version"])
    remit = current_remittance(conn, case_id)
    return {
        "case": dict(case),
        "result": json.loads(case["result_json"] or "{}"),
        "findings": [dict(r) for r in db.list_findings(conn, case_id)],
        "explanation": cached[0] if cached else None,
        "recommendation_version": cached[1] if cached else None,
        "audit": [dict(r) for r in db.list_audit_events(conn, case_id)],
        "reviews": [dict(r) for r in db.list_review_records(conn, case_id)],
        "remittance": json.loads(remit["payload_json"]) if remit else None,
        "remittance_file": remit["file_path"] if remit else None,
    }
