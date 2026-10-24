"""End-to-end journeys through one connection (PRD section 6 user journey).

The AT tests prove each requirement on its own; these chain upload -> AI -> review ->
audit (and rerun) in a single flow and assert every state transition along the way.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend import db, orchestrator
from backend.exceptions import StaleVersionError
from backend.models import ReviewActionType, ReviewRequest
from tests import team_docs as t
from tests.conftest import REFERENCE, fx


def events(conn, case_id: str) -> list[str]:
    return [r["event_type"] for r in db.list_audit_events(conn, case_id)]


def approve(conn, case_id: str, version: int, key: str, **kw):
    return orchestrator.review_action(conn, ReviewRequest(
        case_id=case_id, case_version=version, action=ReviewActionType.APPROVE,
        reason="Request corrected invoice at PO price", idempotency_key=key, **kw))


def test_two_way_price_variance_journey(conn, run) -> None:
    """PRD 6: upload -> findings -> AI suggestion -> approve with it -> audit trail."""
    out = run("inv_AT04.json", "po_AT04.json")
    assert (out.outcome, out.result_state) == ("CREATED", "PRICE_VARIANCE")

    expl, rec_v = orchestrator.request_ai_explanation(conn, out.case_id)
    assert expl.status in ("OK", "AI_ABSTAIN") and rec_v is not None
    assert expl.model_version and expl.prompt_version

    detail = orchestrator.case_detail(conn, out.case_id)
    assert detail["recommendation_version"] == rec_v
    assert detail["explanation"] == expl
    assert {f["check_name"] for f in detail["findings"]} >= {"unit_price", "extension_vs_po"}

    after = approve(conn, out.case_id, 1, "j1", recommendation_version=rec_v)
    assert (after["status"], after["review_status"], after["version"]) == (
        "PRICE_VARIANCE", "RESOLUTION_APPROVED", 2)  # approval never rewrites the finding
    assert after["disposition"] == "NEXT_STEP_APPROVED"
    assert events(conn, out.case_id) == [
        "DOCUMENT_INGESTED", "RULES_EXECUTED", expl.status if expl.status != "OK"
        else "AI_RESULT", "HUMAN_APPROVE"]
    human = db.list_audit_events(conn, out.case_id)[-1]
    assert human["recommendation_version"] == rec_v and human["reviewer_reason"]
    assert len(orchestrator.list_review_records(conn, out.case_id)) == 1


def test_two_way_match_never_auto_approves(conn, run) -> None:
    """PRD P0 two-way mode: a clean match still needs a human; no 820 is generated."""
    out = run("inv_AT01.json", "po_AT01.json")
    c = db.get_case(conn, out.case_id)
    assert (c["status"], c["review_status"], c["disposition_status"]) == (
        "MATCHED_2WAY", "OPEN", "RECEIPT_NOT_VERIFIED")
    assert orchestrator.current_remittance(conn, out.case_id) is None
    assert not {"AUTO_APPROVED", "REMITTANCE_820_GENERATED"} & set(events(conn, out.case_id))


def test_rerun_correction_keeps_history_and_stales_ai(conn, run) -> None:
    """PRD 20.2 / AT22: corrected invoice reruns; v1 findings and AI stay retrievable."""
    out = run("inv_AT04.json", "po_AT04.json")
    _, rec_v1 = orchestrator.request_ai_explanation(conn, out.case_id)
    state = orchestrator.rerun_case(conn, out.case_id,
                                    corrected_invoice_src=fx("inv_AT22_corrected.json"),
                                    reference_dir=REFERENCE)
    c = db.get_case(conn, out.case_id)
    assert (state, c["evidence_version"], c["version"]) == ("MATCHED_2WAY", 2, 2)
    assert orchestrator.get_cached_explanation(conn, out.case_id, 1)[1] == rec_v1
    assert orchestrator.get_cached_explanation(conn, out.case_id, 2) is None
    with pytest.raises(StaleVersionError):
        approve(conn, out.case_id, 2, "j2", recommendation_version=rec_v1)
    rerun = [e for e in db.list_audit_events(conn, out.case_id)
             if e["event_type"] == "RERUN_VERSION_SUPERSEDED"]
    assert [(e["before_state"], e["after_state"]) for e in rerun] == [
        ("PRICE_VARIANCE", "MATCHED_2WAY")]
    assert db.list_findings(conn, out.case_id, 1) and db.list_findings(conn, out.case_id, 2)


def test_three_way_receipt_arrives_then_820_on_disk(conn, monkeypatch) -> None:
    """Rules doc 5.1/5.2: late 861 auto-approves the waiting invoice; 820 never sent."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    out = orchestrator.process(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")]),
                               t.po([(1, "SKU-A", "100", "EA", "2.00")]),
                               reference_dir=REFERENCE)
    assert out.result_state == "WAIT_RECEIPT"
    assert orchestrator.current_remittance(conn, out.case_id) is None
    note = orchestrator.ingest_receipt(conn, t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    assert "reran 1 case" in note
    c = db.get_case(conn, out.case_id)
    assert (c["status"], c["review_status"], c["disposition_status"]) == (
        "MATCHED_3WAY", "AUTO_APPROVED", "AUTO_APPROVED")
    remit = orchestrator.current_remittance(conn, out.case_id)
    on_disk = json.loads(Path(remit["file_path"]).read_text())
    assert on_disk == json.loads(remit["payload_json"])
    assert (on_disk["status"], on_disk["paid_amount"], on_disk["adjustment_amount"]) == (
        "GENERATED_NOT_TRANSMITTED", "200.00", "0.00")
    generated = [e for e in db.list_audit_events(conn, out.case_id)
                 if e["event_type"] == "REMITTANCE_820_GENERATED"]
    assert [json.loads(e["calculations_json"])["transmitted"] for e in generated] == [False]


def test_three_way_short_pay_820_has_adjustment_codes(conn, monkeypatch) -> None:
    """Rules doc 3.2/5.2: a price overcharge short-pays with ADJ_PRICE in the 820."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    out = orchestrator.process(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.10", "210.00")]),
                               t.po([(1, "SKU-A", "100", "EA", "2.00")]),
                               t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]),
                               reference_dir=REFERENCE)
    c = db.get_case(conn, out.case_id)
    assert (c["disposition_status"], c["review_status"]) == (
        "APPROVED_WITH_SHORT_PAY", "AUTO_APPROVED")
    payload = json.loads(orchestrator.current_remittance(conn, out.case_id)["payload_json"])
    assert (payload["paid_amount"], payload["adjustment_amount"]) == ("200.00", "10.00")
    codes = {a["reason_code"] for ln in payload["line_adjustments"] for a in ln["adjustments"]}
    assert codes == {"ADJ_PRICE"}
    assert Decimal(payload["paid_amount"]) + Decimal(payload["adjustment_amount"]) == Decimal(
        payload["invoiced_amount"])


def test_audit_lists_every_case_without_filter(conn, run) -> None:
    """PRD 20.2: the audit history page lists events across all cases."""
    a = run("inv_AT01.json", "po_AT01.json")
    b = run("inv_AT04.json", "po_AT04.json")
    case_ids = {e["case_id"] for e in orchestrator.list_audit_events(conn)}
    assert {a.case_id, b.case_id} <= case_ids


def test_ai_unavailable_does_not_block_approval(conn, run) -> None:
    """PRD 19 / AT16: AI_UNAVAILABLE is not cached and the reviewer can still approve."""
    out = run("inv_AT04.json", "po_AT04.json")
    expl, rec_v = orchestrator.request_ai_explanation(conn, out.case_id,
                                                      provider="mock_unavailable")
    assert (expl.status, rec_v) == ("AI_UNAVAILABLE", None)
    assert orchestrator.get_cached_explanation(conn, out.case_id, 1) is None
    assert approve(conn, out.case_id, 1, "j3")["review_status"] == "RESOLUTION_APPROVED"
