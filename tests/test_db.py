import pytest

from backend import db
from backend.exceptions import AuditPersistenceError
from backend.models import AuditEvent


def test_init_db_idempotent(db_file) -> None:
    db.init_db(db_file).close()
    conn = db.init_db(db_file)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"ingested_documents", "reconciliation_cases", "case_findings", "audit_events",
            "allocations", "ai_explanations", "review_records"} <= tables


def test_transaction_rolls_back_on_audit_failure(conn) -> None:
    db.write_audit_event(conn, AuditEvent(event_type="X", actor="t", correlation_key="k1"))
    with pytest.raises(AuditPersistenceError):
        with db.transaction(conn):
            db.write_audit_event(conn, AuditEvent(event_type="Y", actor="t"))
            db.write_audit_event(conn, AuditEvent(event_type="Z", actor="t",
                                                  correlation_key="k1"))
    types = [r["event_type"] for r in db.list_audit_events(conn)]
    assert types == ["X"]


def test_init_db_adds_sandbox_column_to_old_databases(db_file) -> None:
    """B4 migration: rows from before the sandbox flag default to real (0), never reset."""
    old = db.connect(db_file)
    old.executescript(db.SCHEMA)  # first-release schema has no sandbox columns
    old.execute("INSERT INTO reconciliation_cases (id, status, created_at, updated_at)"
                " VALUES ('CASE-old', 'MATCHED_2WAY', 'now', 'now')")
    old.commit()
    old.close()
    conn = db.init_db(db_file)
    for table in ("reconciliation_cases", "ingested_documents"):
        assert "sandbox" in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    assert db.get_case(conn, "CASE-old")["sandbox"] == 0
