"""Workflow orchestration: ingest, reconcile, explain, review, rerun.

Streamlit pages call these functions; acceptance tests exercise them directly.
Every state change and its audit event are committed in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from backend import ai_resolver, comparator, db, parser, reference
from backend.allocation import get_prior_allocations
from backend.exceptions import (
    ParseError,
    ReviewValidationError,
    StaleVersionError,
)
from backend.models import (
    BLOCKING_STATES,
    AIExplanation,
    AuditEvent,
    CaseResult,
    Invoice,
    PurchaseOrder,
    ResultState,
    ReviewStatus,
    TolerancePolicy,
)

Source = str | Path | bytes

MAX_RULE_ATTEMPTS = 3
ACTION_NOT_SAVED = "Action not saved. Please retry."
STALE_CASE = "Case was updated by another action. Please refresh."
ESCALATION_OWNERS = (
    "operations manager",
    "pricing owner",
    "product-data owner",
    "receiving owner",
)
REVIEW_ACTIONS = {
    "approve": ReviewStatus.RESOLUTION_APPROVED,
    "reject": ReviewStatus.RECOMMENDATION_REJECTED,
    "escalate": ReviewStatus.ESCALATED,
}


@dataclass
class IngestOutcome:
    case_id: str
    outcome: str  # CREATED, REPLAY_NOOP, QUARANTINED, DUPLICATE_CONFLICT
    result_state: str
    message: str = ""


def three_way_enabled() -> bool:
    return os.getenv("THREE_WAY_ENABLED", "false").strip().lower() == "true"


def _bytes(src: Source) -> bytes:
    return src if isinstance(src, bytes) else Path(src).read_bytes()


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def _dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True)


def result_to_dict(result: CaseResult) -> dict[str, Any]:
    data = json.loads(_dumps(asdict(result)))
    data["not_evaluated_checks"] = result.not_evaluated_checks()
    data["ai_bundle"] = ai_resolver.build_evidence_bundle(result)
    return data


# ----------------------------------------------------------------- documents


def _business_key(doc: PurchaseOrder | Invoice) -> str:
    number = doc.po_number if isinstance(doc, PurchaseOrder) else doc.invoice_number
    return f"{doc.buyer}|{doc.supplier}|{number}"


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
           policy_version, result_json) VALUES (?, ?, ?, ?, 'OPEN', '2way', ?, ?, 1, 1, ?, ?, ?)""",
        (case_id, po_id, invoice_id, status, now, now, reference_version, policy_version,
         _dumps(result)),
    )
    return case_id


def _insert_findings(
    conn: sqlite3.Connection, case_id: str, evidence_version: int, result: CaseResult
) -> None:
    now = db.utc_now()
    rows = [(f, None, None) for f in result.header_findings]
    for rec in result.records:
        rows.extend((f, rec.invoice_line, rec.po_line) for f in rec.findings)
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
    po_id: str,
    invoice_id: str,
    result: CaseResult,
) -> None:
    conn.execute("UPDATE allocations SET active = 0 WHERE case_id = ?", (case_id,))
    for alloc in result.allocations:
        conn.execute(
            """INSERT INTO allocations (id, case_id, evidence_version, po_id, po_line,
               invoice_id, quantity, uom, active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (db.new_id("ALLOC"), case_id, evidence_version, po_id, alloc["po_line"],
             invoice_id, alloc["quantity"], alloc["uom"], db.utc_now()),
        )


def _audit(conn: sqlite3.Connection, **kwargs: Any) -> str:
    return db.write_audit_event(conn, AuditEvent(**kwargs))


# ------------------------------------------------------------------- ingestion


def _quarantine(
    conn: sqlite3.Connection, doc_type: str, data: bytes, err: ParseError, actor: str
) -> IngestOutcome:
    error = {"field": err.field_name, "message": err.message, "raw_value": repr(err.raw_value)}
    with db.transaction(conn):
        doc_id = _insert_document(conn, doc_type, data, None, None, "QUARANTINED", _dumps(error))
        case_id = _insert_case(
            conn, ResultState.QUARANTINED.value,
            doc_id if doc_type == "850" else None,
            doc_id if doc_type != "850" else None,
            {"result_state": "QUARANTINED", "error": error},
        )
        _audit(conn, case_id=case_id, event_type="VALIDATION_FAILED", actor=actor,
               after_state="QUARANTINED", doc_hashes=[parser.content_hash(data)],
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
    policy: TolerancePolicy | None = None,
    actor: str = "analyst",
) -> IngestOutcome:
    """Ingest an invoice (plus optional PO and 861) and reconcile it two-way."""
    notes: list[str] = []

    po: PurchaseOrder | None = None
    po_doc_id: str | None = None
    if po_src is not None:
        parsed, po_bytes, bad = _parse_or_quarantine(conn, po_src, "850", actor)
        if bad:
            return bad
        po = parsed  # type: ignore[assignment]
        existing = _find_by_hash(conn, "850", po.content_hash)
        if existing:
            po_doc_id = existing["id"]
        elif (other := _find_by_key(conn, "850", _business_key(po))) is not None:
            return _duplicate_conflict(conn, "850", po_bytes, _business_key(po), other, actor)
        else:
            with db.transaction(conn):
                po_doc_id = _insert_document(conn, "850", po_bytes, _business_key(po),
                                             po.supplier, "VALID")
                _audit(conn, case_id=None, event_type="DOCUMENT_INGESTED", actor=actor,
                       after_state="VALID", doc_hashes=[po.content_hash],
                       calculations={"doc_id": po_doc_id, "doc_type": "850",
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
                   doc_hashes=[invoice.content_hash])
        return IngestOutcome(case["id"], "REPLAY_NOOP", case["status"],
                             "Identical invoice already processed; returning the original case.")
    inv_key = _business_key(invoice)
    if (other := _find_by_key(conn, "810", inv_key)) is not None:
        return _duplicate_conflict(conn, "810", inv_bytes, inv_key, other, actor)

    receipt_hash = None
    if receipt_src is not None:
        receipt = parser.parse_receipt(_bytes(receipt_src))
        receipt_hash = receipt.content_hash
        notes.append("861 receipt stored but unsupported for reconciliation "
                     "(THREE_WAY_ENABLED=false); receipt check NOT_EVALUATED.")

    po_row = _find_by_key(conn, "850", f"{invoice.buyer}|{invoice.supplier}|{invoice.po_number}")
    return _reconcile_new(conn, invoice, inv_bytes, po_row, reference_dir, policy, actor,
                          notes, receipt_src, receipt_hash)


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
            conn, ResultState.DUPLICATE_CONFLICT.value, None,
            doc_id if doc_type == "810" else None,
            {"result_state": "DUPLICATE_CONFLICT", "conflict": detail},
        )
        _audit(conn, case_id=case_id, event_type="DUPLICATE_CONFLICT", actor=actor,
               after_state="DUPLICATE_CONFLICT",
               doc_hashes=[parser.content_hash(data), original["content_hash"]],
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
    policy: TolerancePolicy | None,
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
    policy: TolerancePolicy | None,
) -> tuple[str, dict[str, Any], CaseResult | None, str | None, list[str]]:
    """Return (state, result_dict, CaseResult|None, reference_version, retry_errors)."""
    if po_row is None:
        return (
            ResultState.WAIT_DOCUMENT.value,
            {"result_state": "WAIT_DOCUMENT",
             "missing": f"PO {invoice.po_number} for buyer {invoice.buyer} / supplier "
             f"{invoice.supplier} not found; no cross-partner linking."},
            None, None, [],
        )
    po = parser.parse_po(bytes(po_row["original_bytes"]))
    errors: list[str] = []
    for _attempt in range(MAX_RULE_ATTEMPTS):
        try:
            result, ref_version = _run_rules(po, invoice, po_row["id"], invoice_doc_id, conn,
                                             reference_dir, policy)
            return result.result_state.value, result_to_dict(result), result, ref_version, errors
        except Exception as exc:  # noqa: BLE001 - any rules/reference failure is a hold
            errors.append(f"{type(exc).__name__}: {exc}")
    return (
        ResultState.TECHNICAL_HOLD.value,
        {"result_state": "TECHNICAL_HOLD", "errors": errors,
         "message": f"Rules engine failed after {MAX_RULE_ATTEMPTS} attempts; no pass."},
        None, None, errors,
    )


def _reconcile_new(
    conn: sqlite3.Connection,
    invoice: Invoice,
    inv_bytes: bytes,
    po_row: sqlite3.Row | None,
    reference_dir: str | Path | None,
    policy: TolerancePolicy | None,
    actor: str,
    notes: list[str],
    receipt_src: Source | None,
    receipt_hash: str | None,
) -> IngestOutcome:
    state, result_dict, result, ref_version, errors = _evaluate(
        conn, invoice, po_row, None, reference_dir, policy
    )
    result_dict["notes"] = notes
    hashes = [invoice.content_hash] + ([po_row["content_hash"]] if po_row else [])
    with db.transaction(conn):
        inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                      invoice.supplier, "VALID")
        if receipt_src is not None:
            _insert_document(conn, "861", _bytes(receipt_src), None, invoice.supplier,
                             "STORED_UNSUPPORTED",
                             "P2 three-way matching disabled; stored only.")
        case_id = _insert_case(conn, state, po_row["id"] if po_row else None, inv_doc_id,
                               result_dict, ref_version,
                               result.policy_version if result else None)
        _audit(conn, case_id=case_id, event_type="DOCUMENT_INGESTED", actor=actor,
               after_state="VALID", doc_hashes=[invoice.content_hash]
               + ([receipt_hash] if receipt_hash else []),
               calculations={"doc_id": inv_doc_id, "parser_version": parser.PARSER_VERSION,
                             "notes": notes})
        for err in errors:
            _audit(conn, case_id=case_id, event_type="RULES_RETRY", actor="rules-engine",
                   calculations={"error": err})
        if result is not None and po_row is not None:
            _insert_findings(conn, case_id, 1, result)
            _insert_allocations(conn, case_id, 1, po_row["id"], inv_doc_id, result)
        _audit(conn, case_id=case_id, event_type="RULES_EXECUTED", actor="rules-engine",
               before_state=None, after_state=state, doc_hashes=hashes,
               reference_versions={"reference": ref_version or "n/a",
                                   "policy": result.policy_version if result else "n/a"},
               calculations={"allocations": result.allocations if result else [],
                             "not_evaluated": result.not_evaluated_checks() if result else []})
    return IngestOutcome(case_id, "CREATED", state, "; ".join(notes))


# ----------------------------------------------------------------------- rerun


def rerun_case(
    conn: sqlite3.Connection,
    case_id: str,
    corrected_invoice_src: Source | None = None,
    reference_dir: str | Path | None = None,
    policy: TolerancePolicy | None = None,
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
    po_row = _find_by_key(conn, "850", f"{invoice.buyer}|{invoice.supplier}|{invoice.po_number}")
    state, result_dict, result, ref_version, errors = _evaluate(
        conn, invoice, po_row, case["invoice_id"], reference_dir, policy
    )
    new_ev = case["evidence_version"] + 1
    with db.transaction(conn):
        inv_doc_id = case["invoice_id"]
        if corrected_invoice_src is not None:
            inv_doc_id = _insert_document(conn, "810", inv_bytes, _business_key(invoice),
                                          invoice.supplier, "VALID_REVISION",
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
               doc_hashes=[invoice.content_hash],
               reference_versions={"reference": ref_version or "n/a"},
               calculations={"evidence_version": new_ev,
                             "previous_evidence_version": case["evidence_version"],
                             "stale_recommendations_invalidated": True})
    return state


# -------------------------------------------------------------------------- AI


def get_cached_explanation(
    conn: sqlite3.Connection, case_id: str, evidence_version: int
) -> tuple[AIExplanation, str] | None:
    row = conn.execute(
        "SELECT * FROM ai_explanations WHERE case_id = ? AND evidence_version = ?",
        (case_id, evidence_version),
    ).fetchone()
    if row is None:
        return None
    return AIExplanation(**json.loads(row["explanation_json"])), row["recommendation_version"]


def request_ai_explanation(
    conn: sqlite3.Connection,
    case_id: str,
    stub_mode: bool | None = None,
    generate: ai_resolver.Generate | None = None,
    actor: str = "analyst",
) -> tuple[AIExplanation, str | None]:
    """On-demand, cached per evidence version. AI_UNAVAILABLE results are not cached."""
    case = db.get_case(conn, case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    ev = case["evidence_version"]
    if (cached := get_cached_explanation(conn, case_id, ev)) is not None:
        return cached
    result = json.loads(case["result_json"])
    bundle = result.get("ai_bundle")
    if bundle is None:
        expl = AIExplanation(
            status="AI_ABSTAIN", finding_ids=[], possible_cause="", cited_evidence_ids=[],
            uncertainty_note="No deterministic findings exist for this case; the AI has "
            "nothing to explain.", missing_evidence=[], suggested_next_action="MANUAL_REVIEW",
            model_version="none", prompt_version=ai_resolver.PROMPT_VERSION,
        )
    else:
        expl = ai_resolver.explain(bundle, stub_mode=stub_mode, generate=generate)
    digest = hashlib.sha256(_dumps(expl.to_dict()).encode()).hexdigest()[:8]
    rec_version = f"{case_id}-ev{ev}-{digest}"
    with db.transaction(conn):
        if expl.status != "AI_UNAVAILABLE":
            conn.execute(
                """INSERT INTO ai_explanations (id, case_id, evidence_version,
                   recommendation_version, status, explanation_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (db.new_id("AI"), case_id, ev, rec_version, expl.status,
                 _dumps(expl.to_dict()), db.utc_now()),
            )
        event_type = "AI_RESULT" if expl.status == "OK" else expl.status
        _audit(conn, case_id=case_id, event_type=event_type, actor="ai-resolver",
               before_state=case["status"], after_state=case["status"],
               recommendation_version=rec_version,
               calculations={"model_version": expl.model_version,
                             "prompt_version": expl.prompt_version,
                             "cited_evidence_ids": expl.cited_evidence_ids})
    return expl, (rec_version if expl.status != "AI_UNAVAILABLE" else None)


# ---------------------------------------------------------------------- review


def review_action(
    conn: sqlite3.Connection,
    case_id: str,
    action: str,
    reason: str,
    expected_version: int,
    idempotency_key: str,
    actor: str = "reviewer",
    owner: str | None = None,
    recommendation_version: str | None = None,
    close: bool = False,
) -> sqlite3.Row:
    """Record approve / reject / escalate atomically with its audit event.

    Raises ReviewValidationError, StaleVersionError or AuditPersistenceError; on any
    error nothing is committed.
    """
    if db.audit_key_exists(conn, idempotency_key):
        return db.get_case(conn, case_id)  # retry of an already-committed action
    action = action.lower()
    if action not in REVIEW_ACTIONS:
        raise ReviewValidationError(f"unknown action {action!r}")
    if not reason or not reason.strip():
        raise ReviewValidationError("A reason is required for every review action.")
    if action == "escalate" and (owner or "").lower() not in ESCALATION_OWNERS:
        raise ReviewValidationError(
            f"Escalation requires an accountable owner: {', '.join(ESCALATION_OWNERS)}."
        )
    case = db.get_case(conn, case_id)
    if case is None:
        raise ReviewValidationError("unknown case")
    if case["version"] != expected_version:
        raise StaleVersionError(STALE_CASE)
    if recommendation_version is not None:
        current = get_cached_explanation(conn, case_id, case["evidence_version"])
        if current is None or current[1] != recommendation_version:
            raise StaleVersionError(
                "The AI recommendation is stale for the current evidence version."
            )
    if action == "approve" and close and ResultState(case["status"]) in BLOCKING_STATES:
        raise ReviewValidationError(
            f"Closure blocked: case is {case['status']}; required evidence is missing or "
            "unresolved. Approve a next step instead."
        )

    new_status = REVIEW_ACTIONS[action]
    disposition = None
    if action == "approve":
        disposition = "APPROVED_EXCEPTION_CLOSED" if close else "NEXT_STEP_APPROVED"
    elif action == "escalate":
        disposition = "PENDING_OWNER"

    with db.transaction(conn):
        cur = conn.execute(
            """UPDATE reconciliation_cases SET review_status = ?, disposition = ?,
               version = version + 1, updated_at = ? WHERE id = ? AND version = ?""",
            (new_status.value, disposition, db.utc_now(), case_id, expected_version),
        )
        if cur.rowcount != 1:
            raise StaleVersionError(STALE_CASE)
        _audit(conn, case_id=case_id, event_type=f"HUMAN_{action.upper()}", actor=actor,
               before_state=f"{case['status']}/{case['review_status']}",
               after_state=f"{case['status']}/{new_status.value}",
               recommendation_version=recommendation_version, reviewer_reason=reason.strip(),
               escalation_owner=owner if action == "escalate" else None,
               idempotency_key=idempotency_key,
               calculations={"disposition": disposition,
                             "evidence_version": case["evidence_version"],
                             "case_version": expected_version + 1})
    return db.get_case(conn, case_id)


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
    }
