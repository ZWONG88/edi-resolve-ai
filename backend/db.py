"""SQLite schema and low-level persistence. Audit writes are always transactional."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.exceptions import AuditPersistenceError
from backend.models import AuditEvent, ReviewRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested_documents (
    id TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL,
    original_bytes_path TEXT,
    original_bytes BLOB NOT NULL,
    content_hash TEXT NOT NULL,
    business_key TEXT,
    ingested_at TEXT NOT NULL,
    tenant TEXT,
    trading_partner TEXT,
    parser_version TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    validation_errors TEXT
);
CREATE INDEX IF NOT EXISTS ix_docs_hash ON ingested_documents(content_hash);
CREATE INDEX IF NOT EXISTS ix_docs_key ON ingested_documents(doc_type, business_key);

CREATE TABLE IF NOT EXISTS reconciliation_cases (
    id TEXT PRIMARY KEY,
    po_id TEXT,
    invoice_id TEXT,
    status TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'OPEN',
    disposition TEXT,
    mode TEXT NOT NULL DEFAULT '2way',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    evidence_version INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    reference_version TEXT,
    policy_version TEXT,
    result_json TEXT
);

CREATE TABLE IF NOT EXISTS case_findings (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES reconciliation_cases(id),
    evidence_version INTEGER NOT NULL,
    finding_type TEXT,
    check_name TEXT NOT NULL,
    check_status TEXT NOT NULL,
    invoice_line TEXT,
    po_line TEXT,
    po_value TEXT,
    invoice_value TEXT,
    normalized_po_value TEXT,
    normalized_invoice_value TEXT,
    delta TEXT,
    tolerance_applied TEXT,
    message TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS allocations (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_version INTEGER NOT NULL,
    po_id TEXT NOT NULL,
    po_line TEXT NOT NULL,
    invoice_id TEXT NOT NULL,
    quantity TEXT NOT NULL,
    uom TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_explanations (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    evidence_version INTEGER NOT NULL,
    recommendation_version TEXT NOT NULL,
    status TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (case_id, evidence_version)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    case_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_at TEXT NOT NULL,
    before_state TEXT,
    after_state TEXT,
    doc_hashes TEXT,
    source_pointers TEXT,
    findings TEXT,
    policy_version TEXT,
    reference_version TEXT,
    calculations_json TEXT,
    recommendation_version TEXT,
    reviewer_reason TEXT,
    escalation_owner TEXT,
    idempotency_key TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS review_records (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES reconciliation_cases(id),
    case_version INTEGER NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    escalation_owner TEXT,
    event_at TEXT NOT NULL,
    audit_event_id TEXT NOT NULL REFERENCES audit_events(id)
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def db_path() -> str:
    return get_settings().database_path


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path or db_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Create all tables if missing. Idempotent; safe on every app start."""
    conn = connect(path)
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All-or-nothing unit of work. Any exception rolls everything back."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True)


def write_audit_event(conn: sqlite3.Connection, event: AuditEvent) -> str:
    """Insert one audit event inside the caller's transaction.

    Raises AuditPersistenceError on any storage failure so the caller's
    transaction rolls back and the action is reported as not saved.
    """
    try:
        conn.execute(
            """INSERT INTO audit_events (id, case_id, event_type, actor, event_at,
               before_state, after_state, doc_hashes, source_pointers, findings,
               policy_version, reference_version, calculations_json,
               recommendation_version, reviewer_reason, escalation_owner, idempotency_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id, event.case_id, event.event_type, event.actor,
                event.utc_timestamp.isoformat(), event.before_state, event.after_state,
                _dumps(event.doc_hashes or {}), _dumps(event.source_pointers or []),
                event.findings, event.policy_version, event.reference_version,
                _dumps(event.calculations or {}), event.recommendation_version,
                event.reason, event.escalation_owner, event.correlation_key,
            ),
        )
    except sqlite3.Error as exc:
        raise AuditPersistenceError(f"audit write failed: {exc}") from exc
    return event.event_id


def write_review_record(conn: sqlite3.Connection, record: ReviewRecord) -> None:
    """Persist a review action; call inside the same transaction as its audit event."""
    conn.execute(
        """INSERT INTO review_records (id, case_id, case_version, action, reason,
           escalation_owner, event_at, audit_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (record.review_id, record.case_id, record.case_version, record.action.value,
         record.reason, record.escalation_owner, record.utc_timestamp.isoformat(),
         record.audit_event_id),
    )


def list_review_records(conn: sqlite3.Connection, case_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM review_records WHERE case_id = ? ORDER BY event_at, rowid", (case_id,)
    ).fetchall()


def audit_key_exists(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT 1 FROM audit_events WHERE idempotency_key = ?", (key,)).fetchone()
    return row is not None


def get_case(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM reconciliation_cases WHERE id = ?", (case_id,)).fetchone()


def list_cases(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM reconciliation_cases ORDER BY created_at DESC"
    ).fetchall()


def list_findings(
    conn: sqlite3.Connection, case_id: str, evidence_version: int | None = None
) -> list[sqlite3.Row]:
    if evidence_version is None:
        case = get_case(conn, case_id)
        evidence_version = case["evidence_version"] if case else 0
    return conn.execute(
        "SELECT * FROM case_findings WHERE case_id = ? AND evidence_version = ? ORDER BY id",
        (case_id, evidence_version),
    ).fetchall()


def list_audit_events(conn: sqlite3.Connection, case_id: str | None = None) -> list[sqlite3.Row]:
    if case_id:
        return conn.execute(
            "SELECT * FROM audit_events WHERE case_id = ? ORDER BY event_at, rowid", (case_id,)
        ).fetchall()
    return conn.execute("SELECT * FROM audit_events ORDER BY event_at, rowid").fetchall()


def get_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM ingested_documents WHERE id = ?", (doc_id,)).fetchone()
