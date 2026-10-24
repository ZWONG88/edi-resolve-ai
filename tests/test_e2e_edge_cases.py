"""Edge-case entry points and known bugs found by the e2e audit (see e2e-test-plan.md).

Known bugs are pinned with xfail(strict=True): the test asserts the correct behaviour,
so it starts passing (and fails the run as XPASS) the moment the bug is fixed.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from backend import db, orchestrator
from backend.exceptions import ReviewValidationError
from backend.models import ReviewActionType, ReviewRequest
from tests import team_docs as t
from tests.conftest import DATASETS, REFERENCE, fx


def events(conn, case_id: str) -> list[str]:
    return [r["event_type"] for r in db.list_audit_events(conn, case_id)]


@pytest.mark.xfail(strict=True, reason="B1: REJECT leaves an auto-approved 820 live")
def test_reject_auto_approved_supersedes_820(conn, monkeypatch) -> None:
    """Rules doc 5.2: a rejected invoice must not keep a payable 820 on file."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    out = orchestrator.process(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")]),
                               t.po([(1, "SKU-A", "100", "EA", "2.00")]),
                               t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]),
                               reference_dir=REFERENCE)
    assert orchestrator.current_remittance(conn, out.case_id) is not None
    orchestrator.review_action(conn, ReviewRequest(
        case_id=out.case_id, case_version=1, action=ReviewActionType.REJECT,
        reason="Wrong vendor remit-to", idempotency_key="b1"))
    assert orchestrator.current_remittance(conn, out.case_id) is None


@pytest.mark.xfail(strict=True, raises=Exception,
                   reason="B2: rerun of a quarantined case raises a raw ParseError")
def test_rerun_quarantined_raises_review_validation_error(conn, run) -> None:
    """rerun_case contract: unrerunnable cases raise ReviewValidationError."""
    out = run("inv_AT11_malformed.json")
    with pytest.raises(ReviewValidationError):
        orchestrator.rerun_case(conn, out.case_id, reference_dir=REFERENCE)


@pytest.mark.xfail(strict=True, reason="B5: summarize() totals are not formatted to cents")
def test_summarize_totals_two_decimals(conn, monkeypatch) -> None:
    """PRD 22.2 batch summary: money totals render as fixed two-decimal strings."""
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


def test_rerun_duplicate_conflict_does_not_double_allocate(conn, run) -> None:
    """AT08/AT09 safety: rerunning a conflict case never over-allocates the PO."""
    run("inv_AT09.json", "po_AT09.json")
    conflict = run("inv_AT09_conflict.json")
    orchestrator.rerun_case(conn, conflict.case_id, reference_dir=REFERENCE)
    total = sum(Decimal(r["quantity"]) for r in conn.execute(
        "SELECT quantity FROM allocations WHERE active = 1"))
    po_qty = sum(Decimal(ln["quantity"]) for ln in json.loads(
        fx("po_AT09.json").read_text())["lines"])
    assert total <= po_qty
