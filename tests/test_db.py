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
