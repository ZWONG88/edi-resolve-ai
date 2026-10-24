"""Edge-case entry points and the bugs found by the e2e audit (B1-B6, e2e-test-plan.md)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend import db, orchestrator
from backend.exceptions import AuditPersistenceError, ReviewValidationError
from backend.models import ReviewActionType, ReviewRequest
from tests import team_docs as t
from tests.conftest import DATASETS, REFERENCE


def events(conn, case_id: str) -> list[str]:
    return [r["event_type"] for r in db.list_audit_events(conn, case_id)]


def review(conn, case_id: str, action: str, version: int, key: str, **kw):
    return orchestrator.review_action(conn, ReviewRequest(
        case_id=case_id, case_version=version, action=ReviewActionType(action),
        reason="reviewer reason", idempotency_key=key, **kw))


def three_way_clean(conn, number: str = "1", sandbox: bool = False):
    return orchestrator.process(
        conn, t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")], number=f"INV-{number}",
                        po_number=f"PO-{number}"),
        t.po([(1, "SKU-A", "100", "EA", "2.00")], number=f"PO-{number}"),
        t.receipt([(1, "SKU-A", "100", "100", "0", "EA")], number=f"RA-{number}",
                  po_number=f"PO-{number}"),
        reference_dir=REFERENCE, sandbox=sandbox)


def test_reject_voids_820_and_reapproval_restores_it(conn, monkeypatch) -> None:
    """B1, rules doc 5.2: a rejected invoice keeps no payable 820; re-approval regenerates."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    out = three_way_clean(conn)
    first = orchestrator.current_remittance(conn, out.case_id)
    assert first is not None
    review(conn, out.case_id, "REJECT", 1, "b1-r")
    assert orchestrator.current_remittance(conn, out.case_id) is None
    voided = [e for e in db.list_audit_events(conn, out.case_id)
              if e["event_type"] == "REMITTANCE_820_VOIDED"]
    assert [json.loads(e["calculations_json"])["remittance_id"] for e in voided] == [
        first["id"]]
    review(conn, out.case_id, "APPROVE", 2, "b1-a")
    again = orchestrator.current_remittance(conn, out.case_id)
    assert again is not None and again["id"] != first["id"]
    assert again["paid_amount"] == "200.00"


def test_escalate_voids_820(conn, monkeypatch) -> None:
    """B1: an escalated invoice is in question, so its 820 is voided too."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    out = three_way_clean(conn)
    review(conn, out.case_id, "ESCALATE", 1, "b1-e", escalation_owner="pricing owner")
    assert orchestrator.current_remittance(conn, out.case_id) is None


def test_rerun_quarantined_raises_review_validation_error(conn, run) -> None:
    """B2, rerun_case contract: unrerunnable cases raise ReviewValidationError."""
    out = run("inv_AT11_malformed.json")
    with pytest.raises(ReviewValidationError):
        orchestrator.rerun_case(conn, out.case_id, reference_dir=REFERENCE)


def test_summarize_totals_two_decimals(conn, monkeypatch) -> None:
    """B5, PRD 22.2 batch summary: money totals render as fixed two-decimal strings."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    summary = orchestrator.import_dataset(conn, DATASETS / "dataset2",
                                          reference_dir=DATASETS / "reference")
    assert summary["totals"] == {"invoiced": "23614.20", "approved": "19106.30",
                                 "blocked": "524.80", "held": "3983.10"}


def test_reference_load_failure_is_technical_hold(conn, run, monkeypatch) -> None:
    """PRD 20.3: a reference outage is a bounded-retry TECHNICAL_HOLD, never a pass."""
    def down(*args, **kwargs):
        raise FileNotFoundError("uom_conversions.json")

    monkeypatch.setattr(orchestrator.reference, "load_uom_conversions", down)
    out = run("inv_AT02.json", "po_AT02.json")
    assert out.result_state == "TECHNICAL_HOLD"
    assert events(conn, out.case_id).count("RULES_RETRY") == 3
    assert conn.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 0
    assert db.list_findings(conn, out.case_id) == []


def test_ai_on_case_without_findings_abstains(conn, run) -> None:
    """PRD 19: no deterministic findings means the AI has nothing to explain."""
    out = run("inv_AT10_missing_po.json")
    assert out.result_state == "WAIT_DOCUMENT"
    expl, rec_v = orchestrator.request_ai_explanation(conn, out.case_id)
    assert (expl.status, expl.model_version) == ("AI_ABSTAIN", "none")
    assert rec_v is not None and "AI_ABSTAIN" in events(conn, out.case_id)


def test_rerun_duplicate_conflict_refused(conn, run) -> None:
    """B3, AT09: a conflict stays held; rerunning it never reconciles the duplicate."""
    run("inv_AT09.json", "po_AT09.json")
    conflict = run("inv_AT09_conflict.json")
    before = conn.execute("SELECT id, quantity FROM allocations WHERE active = 1").fetchall()
    with pytest.raises(ReviewValidationError, match="Duplicate conflict"):
        orchestrator.rerun_case(conn, conflict.case_id, reference_dir=REFERENCE)
    after = conn.execute("SELECT id, quantity FROM allocations WHERE active = 1").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]
    assert db.get_case(conn, conflict.case_id)["status"] == "DUPLICATE_CONFLICT"


def test_idempotency_key_reused_for_other_action_refused(conn, run) -> None:
    """B6, PRD 20.1: a retry key replays only the same action on the same case."""
    a = run("inv_AT04.json", "po_AT04.json")
    b = run("inv_AT01.json", "po_AT01.json")
    review(conn, a.case_id, "APPROVE", 1, "b6")
    review(conn, a.case_id, "APPROVE", 1, "b6")  # true retry: no-op
    with pytest.raises(ReviewValidationError, match="Idempotency key"):
        review(conn, a.case_id, "REJECT", 2, "b6")
    with pytest.raises(ReviewValidationError, match="Idempotency key"):
        review(conn, b.case_id, "APPROVE", 1, "b6")
    human = [e for e in orchestrator.list_audit_events(conn) if e["event_type"].startswith(
        "HUMAN_")]
    assert len(human) == 1
    assert db.get_case(conn, b.case_id)["version"] == 1


def test_reset_deletes_only_test_data(conn, monkeypatch) -> None:
    """B4, PRD 20.2: reset clears sandbox cases; real cases and their history survive."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    test_case = three_way_clean(conn, "1", sandbox=True)
    test_file = Path(orchestrator.current_remittance(conn, test_case.case_id)["file_path"])
    real = three_way_clean(conn, "2")
    shared_po = t.po([(1, "SKU-A", "100", "EA", "2.00")], number="PO-3")
    orchestrator.process(conn, t.invoice([(1, "SKU-A", "50", "EA", "2.00", "100.00")],
                                         number="INV-3a", po_number="PO-3"),
                         shared_po, reference_dir=REFERENCE, sandbox=True)
    real_on_test_po = orchestrator.process(
        conn, t.invoice([(1, "SKU-A", "50", "EA", "2.00", "100.00")], number="INV-3b",
                        po_number="PO-3"), reference_dir=REFERENCE)
    real_ids = {real.case_id, real_on_test_po.case_id}
    real_events = {e["id"] for e in orchestrator.list_audit_events(conn)
                   if e["case_id"] in real_ids}

    counts = orchestrator.reset_test_data(conn)

    assert {c["id"] for c in orchestrator.list_cases(conn)} == real_ids
    assert real_events <= {e["id"] for e in orchestrator.list_audit_events(conn)}
    assert not test_file.exists()
    assert orchestrator.current_remittance(conn, real.case_id) is not None
    kept_po = db.get_case(conn, real_on_test_po.case_id)["po_id"]
    assert db.get_document(conn, kept_po) is not None  # sandbox PO a real case needs
    assert counts["cases"] == 2 and counts["documents_kept"] == 1
    resets = [e for e in orchestrator.list_audit_events(conn)
              if e["event_type"] == "TEST_DATA_RESET"]
    assert len(resets) == 1
    again = orchestrator.reset_test_data(conn)
    assert (again["cases"], again["documents"], again["audit_events"]) == (0, 0, 0)
    assert orchestrator.rerun_case(conn, real.case_id, reference_dir=REFERENCE) == (
        "MATCHED_3WAY")


def test_ai_audit_failure_rolls_back_explanation(conn, run, monkeypatch) -> None:
    """PRD 20.2: if the AI audit event cannot be saved, the explanation is not cached."""
    out = run("inv_AT04.json", "po_AT04.json")
    real = db.write_audit_event

    def failing(c, event):
        if event.actor == "ai-resolver":
            raise AuditPersistenceError("disk full")
        return real(c, event)

    monkeypatch.setattr(db, "write_audit_event", failing)
    with pytest.raises(AuditPersistenceError):
        orchestrator.request_ai_explanation(conn, out.case_id)
    assert orchestrator.get_cached_explanation(conn, out.case_id, 1) is None
    monkeypatch.setattr(db, "write_audit_event", real)
    expl, rec_v = orchestrator.request_ai_explanation(conn, out.case_id)
    assert rec_v is not None and orchestrator.get_cached_explanation(conn, out.case_id, 1)
