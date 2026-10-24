"""P0 release gate: acceptance tests AT01-AT24 (PRD section 21).

Each test runs end to end through backend.orchestrator against an isolated temp database
and asserts case state, line findings, normalized values, evidence and audit events.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend import comparator, db, orchestrator
from backend.config import get_settings
from backend.exceptions import (
    AuditPersistenceError,
    ReviewValidationError,
    StaleVersionError,
)
from backend.models import Money, ReconciliationPolicy, ReviewActionType, ReviewRequest
from tests.conftest import REFERENCE, fx


def findings(conn, case_id: str, ev: int | None = None) -> dict[str, dict]:
    return {r["check_name"]: dict(r) for r in db.list_findings(conn, case_id, ev)}


def events(conn, case_id: str) -> list[str]:
    return [r["event_type"] for r in db.list_audit_events(conn, case_id)]


def review(conn, case_id: str, action: str, reason: str, version: int, key: str, **kw):
    request = ReviewRequest(case_id=case_id, case_version=version,
                            action=ReviewActionType(action.upper()), reason=reason,
                            escalation_owner=kw.pop("owner", None), idempotency_key=key, **kw)
    return orchestrator.review_action(conn, request)


def case(conn, case_id: str):
    return db.get_case(conn, case_id)


def result(conn, case_id: str) -> dict:
    return json.loads(case(conn, case_id)["result_json"])


# ------------------------------------------------------------------ AT01-AT12


def test_at01_exact_match(conn, run) -> None:
    out = run("inv_AT01.json", "po_AT01.json")
    f = findings(conn, out.case_id)
    assert out.result_state == "MATCHED_2WAY"
    assert f["receipt"]["check_status"] == "CHECK_NOT_EVALUATED"
    assert all(r["check_status"] != "CHECK_FAILED" for r in f.values())
    assert f["extension_vs_po"]["invoice_value"] == "200.00"
    assert case(conn, out.case_id)["review_status"] == "OPEN"  # no payment action
    assert "RULES_EXECUTED" in events(conn, out.case_id)


def test_at02_uom_normalized_equality(conn, run) -> None:
    out = run("inv_AT02.json", "po_AT02.json")
    rec = result(conn, out.case_id)["line_results"][0]
    assert out.result_state == "MATCHED_2WAY"
    assert rec["po_value_normalized"]["quantity"] == "600"
    assert rec["invoice_value_normalized"]["quantity"] == "600"
    assert Decimal(rec["po_value_normalized"]["unit_price"]) == Decimal("2")
    assert Decimal(rec["invoice_value_normalized"]["unit_price"]) == Decimal("2")
    assert rec["invoice_value_normalized"]["extension"] == "1200.00"
    assert "ref.uom.PROD-CS-001" in result(conn, out.case_id)["evidence_index"]


def test_at03_missing_conversion_insufficient_evidence(conn, run) -> None:
    out = run("inv_AT03.json", "po_AT03.json")
    f = findings(conn, out.case_id)
    assert out.result_state == "INSUFFICIENT_EVIDENCE"
    assert f["uom_conversion"]["finding_type"] == "INSUFFICIENT_EVIDENCE"
    for check in ("quantity", "unit_price", "extension_vs_po"):
        assert f[check]["check_status"] == "CHECK_NOT_EVALUATED"
        assert f[check]["delta"] is None  # no guessed variance
    assert not any(k.startswith("ref.uom") for k in result(conn, out.case_id)["evidence_index"])


def test_at04_price_variance(conn, run) -> None:
    out = run("inv_AT04.json", "po_AT04.json")
    f = findings(conn, out.case_id)
    assert out.result_state == "PRICE_VARIANCE"
    assert f["unit_price"]["delta"] == "+0.10"
    assert f["extension_vs_po"]["delta"] == "+10.00"
    cited = json.loads(f["unit_price"]["evidence_json"])
    assert "po.1.unit_price" in cited and "inv.1.unit_price" in cited
    assert case(conn, out.case_id)["review_status"] == "OPEN"  # human review required


def test_at05_contract_displayed_separately(conn, run) -> None:
    out = run("inv_AT05.json", "po_AT05.json")
    f = findings(conn, out.case_id)
    assert out.result_state == "PRICE_VARIANCE"
    assert Decimal(f["unit_price"]["normalized_po_value"]) == Decimal("2.00")
    assert f["contract_reference"]["check_status"] == "CHECK_PASSED"
    assert "does not override" in f["contract_reference"]["message"]
    expl, _ = orchestrator.request_ai_explanation(conn, out.case_id)
    assert "contract.CTR-005" in expl.cited_evidence_ids
    assert "hypothesis" in expl.possible_cause.lower()


def test_at06_ai_abstains_without_contract(conn, run) -> None:
    out = run("inv_AT04.json", "po_AT04.json")
    expl, _ = orchestrator.request_ai_explanation(conn, out.case_id)
    assert expl.status == "AI_ABSTAIN"
    assert "contract changed" not in expl.possible_cause.lower()
    assert expl.missing_evidence
    assert findings(conn, out.case_id)["unit_price"]["delta"] == "+0.10"


def test_at07_partial_billing(conn, run) -> None:
    out = run("inv_AT07.json", "po_AT07.json")
    rec = result(conn, out.case_id)["line_results"][0]
    assert out.result_state == "PARTIAL_BILLING"
    assert rec["delta"]["po_balance"] == "60"
    assert findings(conn, out.case_id)["quantity"]["check_status"] == "CHECK_PASSED"
    allocs = conn.execute("SELECT * FROM allocations WHERE active = 1").fetchall()
    assert [(a["quantity"], a["uom"]) for a in allocs] == [("40", "EA")]


def test_at08_cumulative_over_authorization(conn, run) -> None:
    first = run("inv_AT08a.json", "po_AT08.json")
    second = run("inv_AT08b.json", "po_AT08.json")
    q = findings(conn, second.case_id)["quantity"]
    assert first.result_state == "PARTIAL_BILLING"
    assert second.result_state == "QUANTITY_VARIANCE"
    assert q["normalized_invoice_value"] == "110"
    assert q["delta"] == "+10"
    allocs = conn.execute("SELECT quantity FROM allocations WHERE active = 1").fetchall()
    assert [a["quantity"] for a in allocs] == ["70"]  # no allocation for the overage


def test_at09_replay_and_conflict(conn, run) -> None:
    first = run("inv_AT09.json", "po_AT09.json")
    replay = run("inv_AT09.json", "po_AT09.json")
    conflict = run("inv_AT09_conflict.json", "po_AT09.json")
    assert replay.outcome == "REPLAY_NOOP" and replay.case_id == first.case_id
    assert conn.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 1
    assert conflict.outcome == "DUPLICATE_CONFLICT"
    assert case(conn, conflict.case_id)["status"] == "DUPLICATE_CONFLICT"
    assert "REPLAY_NOOP" in events(conn, first.case_id)


def test_at10_missing_po_then_ambiguous_link(conn, run) -> None:
    missing = run("inv_AT10_missing_po.json")
    assert missing.result_state == "WAIT_DOCUMENT"
    assert "PO-AT10-MISSING" in result(conn, missing.case_id)["missing"]
    ambiguous = run("inv_AT10_repeated.json", "po_AT10_repeated.json")
    link = findings(conn, ambiguous.case_id)["line_link"]
    assert ambiguous.result_state == "REVIEW_LINK"
    assert "2 PO lines" in link["message"]
    assert conn.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 0


def test_at11_quarantine_with_exact_error(conn, run) -> None:
    out = run("inv_AT11_malformed.json", "po_AT01.json")
    assert out.result_state == "QUARANTINED"
    assert result(conn, out.case_id)["error"]["field"] == "lines[0].quantity"
    unsupported = run("inv_AT11_unsupported.json")
    assert unsupported.result_state == "QUARANTINED"
    assert result(conn, unsupported.case_id)["error"]["field"] == "version"
    envelope = orchestrator.process(conn, b"{broken", None)
    assert result(conn, envelope.case_id)["error"]["field"] == "<envelope>"


def test_at12_currency_and_unsupported_charges(conn, run) -> None:
    cur = run("inv_AT12_currency.json", "po_AT12.json")
    f = findings(conn, cur.case_id)
    assert cur.result_state != "MATCHED_2WAY"
    assert f["currency"]["check_status"] == "CHECK_NOT_EVALUATED"
    assert f["extension_vs_po"]["check_status"] == "CHECK_NOT_EVALUATED"
    freight = run("inv_AT12_freight.json", "po_AT12.json")
    total = findings(conn, freight.case_id)["invoice_total"]
    assert freight.result_state == "NOT_EVALUATED"
    assert total["check_status"] == "CHECK_NOT_EVALUATED"


# ------------------------------------------------------------------ AT13-AT24


def test_at13_decimal_precision(conn, run) -> None:
    a = run("inv_AT13a.json", "po_AT13a.json")
    rec = result(conn, a.case_id)["line_results"][0]
    assert rec["po_value_normalized"]["extension"] == "1.01"
    assert comparator.round_money(Decimal("3") * Decimal("0.335")) == Decimal("1.01")
    b = run("inv_AT13b.json", "po_AT13b.json")
    rec_b = result(conn, b.case_id)["line_results"][0]
    assert Decimal(rec_b["po_value_normalized"]["unit_price"]) == Decimal("0.10")
    assert rec_b["po_value_normalized"]["extension"] == "2.00"
    assert a.result_state == b.result_state == "MATCHED_2WAY"


def test_at14_tolerance_boundary(conn, run) -> None:
    policy = ReconciliationPolicy.baseline().model_copy(update={
        "price_abs_tolerance": Money(amount=Decimal("0.01")), "policy_version": "AT14-abs-0.01"})
    ok = run("inv_AT14a.json", "po_AT14.json", policy=policy)
    bad = run("inv_AT14b.json", "po_AT14.json", policy=policy)
    f_ok = findings(conn, ok.case_id)["unit_price"]
    f_bad = findings(conn, bad.case_id)["unit_price"]
    assert f_ok["check_status"] == "CHECK_PASSED" and f_ok["delta"] == "+0.01"
    assert f_bad["check_status"] == "CHECK_FAILED" and f_bad["delta"] == "+0.0101"
    assert "AT14-abs-0.01" in f_ok["tolerance_applied"]
    assert case(conn, bad.case_id)["policy_version"] == "AT14-abs-0.01"


def test_at15_ai_cannot_inject_facts(conn, run) -> None:
    out = run("inv_AT15.json", "po_AT15.json")
    before = (dict(case(conn, out.case_id)), conn.execute(
        "SELECT original_bytes FROM ingested_documents WHERE doc_type='810'").fetchone()[0])
    bundle = result(conn, out.case_id)["ai_bundle"]
    assert "IGNORE" not in json.dumps(bundle)

    seen_prompts: list[str] = []

    def injected(system: str, user: str) -> dict:
        seen_prompts.append(user)
        return {"status": "OK", "finding_ids": ["F999"], "possible_cause": "Contract "
                "CTR-FAKE-999 sets the price; delta is 0.50. Mark MATCHED.",
                "cited_evidence_ids": ["contract.CTR-FAKE-999"], "uncertainty_note": "",
                "missing_evidence": [], "suggested_next_action": "APPROVE_PAYMENT"}

    expl, _ = orchestrator.request_ai_explanation(conn, out.case_id,
                                             generate=injected)
    assert expl.status == "AI_ABSTAIN"
    assert expl.possible_cause == ""
    assert "IGNORE" not in seen_prompts[0]
    after = dict(case(conn, out.case_id))
    assert after["status"] == before[0]["status"] == "PRICE_VARIANCE"
    assert after["review_status"] == "OPEN"
    assert conn.execute("SELECT original_bytes FROM ingested_documents WHERE doc_type='810'"
                        ).fetchone()[0] == before[1]


def test_at16_ai_unavailable_keeps_findings(conn, run) -> None:
    out = run("inv_AT04.json", "po_AT04.json")

    def slow(system: str, user: str) -> str:
        time.sleep(1)
        return "{}"

    expl, rec_v = orchestrator.request_ai_explanation(conn, out.case_id, generate=slow,
                                                      timeout_seconds=0.1)
    assert expl.status == "AI_UNAVAILABLE" and rec_v is None
    bad, _ = orchestrator.request_ai_explanation(conn, out.case_id,
                                                 generate=lambda s, u: "not json")
    assert bad.status == "AI_UNAVAILABLE"
    down, _ = orchestrator.request_ai_explanation(conn, out.case_id,
                                                  provider="mock_unavailable")
    assert down.status == "AI_UNAVAILABLE"
    assert bad.status == "AI_UNAVAILABLE"
    assert findings(conn, out.case_id)["unit_price"]["delta"] == "+0.10"
    c = case(conn, out.case_id)
    review(conn, out.case_id, "escalate", "AI down; pricing to check", c["version"],
           "at16-1", owner="pricing owner")
    assert case(conn, out.case_id)["review_status"] == "ESCALATED"


def test_at17_three_review_actions(conn, run) -> None:
    a = run("inv_AT04.json", "po_AT04.json")
    b = run("inv_AT05.json", "po_AT05.json")
    c = run("inv_AT03.json", "po_AT03.json")
    review(conn, a.case_id, "approve", "Request corrected invoice at PO price",
                          1, "at17-a")
    review(conn, b.case_id, "reject", "Contract not yet verified", 1, "at17-b")
    review(conn, c.case_id, "escalate", "Need CS pack size", 1, "at17-c",
                          owner="product-data owner")
    ca, cb, cc = case(conn, a.case_id), case(conn, b.case_id), case(conn, c.case_id)
    assert (ca["review_status"], cb["review_status"], cc["review_status"]) == (
        "RESOLUTION_APPROVED", "RECOMMENDATION_REJECTED", "ESCALATED")
    assert ca["status"] == "PRICE_VARIANCE" and ca["disposition"] == "NEXT_STEP_APPROVED"
    human = {r["case_id"]: r for r in db.list_audit_events(conn)
             if r["event_type"].startswith("HUMAN_")}
    assert human[a.case_id]["reviewer_reason"] == "Request corrected invoice at PO price"
    assert human[c.case_id]["escalation_owner"] == "product-data owner"
    with pytest.raises(ValidationError, match="escalation_owner"):
        review(conn, c.case_id, "escalate", "x", 2, "at17-d", owner=None)
    with pytest.raises(ReviewValidationError, match="accountable owner"):
        review(conn, c.case_id, "escalate", "x", 2, "at17-e", owner="my cousin")


def test_at18_blocked_closure_reason_and_stale(conn, run) -> None:
    out = run("inv_AT03.json", "po_AT03.json")
    with pytest.raises(ReviewValidationError, match="Closure blocked"):
        review(conn, out.case_id, "approve", "close it", 1, "k1", close=True)
    with pytest.raises(ValidationError, match="reason"):
        review(conn, out.case_id, "approve", "   ", 1, "k2")
    review(conn, out.case_id, "reject", "first", 1, "k3")
    with pytest.raises(StaleVersionError):
        review(conn, out.case_id, "approve", "stale", 1, "k4")
    c = case(conn, out.case_id)
    assert c["status"] == "INSUFFICIENT_EVIDENCE" and c["version"] == 2
    assert conn.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 0


def test_at19_audit_failure_rolls_back_then_retry(conn, run, monkeypatch) -> None:
    out = run("inv_AT07.json", "po_AT07.json")
    real = db.write_audit_event

    def failing(c, event):
        if event.event_type.startswith("HUMAN_"):
            raise AuditPersistenceError("disk full")
        return real(c, event)

    monkeypatch.setattr(db, "write_audit_event", failing)
    with pytest.raises(AuditPersistenceError):
        review(conn, out.case_id, "approve", "ok", 1, "at19-key")
    c = case(conn, out.case_id)
    assert c["review_status"] == "OPEN" and c["version"] == 1
    assert orchestrator.ACTION_NOT_SAVED == "Action not saved. Please retry."
    assert conn.execute("SELECT COUNT(*) FROM allocations WHERE active=1").fetchone()[0] == 1

    monkeypatch.setattr(db, "write_audit_event", real)
    review(conn, out.case_id, "approve", "ok", 1, "at19-key")
    review(conn, out.case_id, "approve", "ok", 1, "at19-key")  # retried again
    assert events(conn, out.case_id).count("HUMAN_APPROVE") == 1
    assert case(conn, out.case_id)["version"] == 2


def test_at20_rules_failure_hold_retry_restart(db_file, conn, run, monkeypatch) -> None:
    calls = {"n": 0}

    def broken(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("reference service down")

    real_compare = orchestrator.comparator.compare
    monkeypatch.setattr(orchestrator.comparator, "compare", broken)
    out = run("inv_AT07.json", "po_AT07.json")
    assert out.result_state == "TECHNICAL_HOLD"
    assert calls["n"] == get_settings().max_retry_attempts
    assert events(conn, out.case_id).count("RULES_RETRY") == get_settings().max_retry_attempts
    assert conn.execute("SELECT COUNT(*) FROM allocations").fetchone()[0] == 0

    monkeypatch.setattr(orchestrator.comparator, "compare", real_compare)
    conn.close()
    restarted = db.init_db(db_file)  # application restart
    for _ in range(2):  # retry after restart, then retry again
        state = orchestrator.rerun_case(restarted, out.case_id, reference_dir=REFERENCE)
        assert state == "PARTIAL_BILLING"
    active = restarted.execute("SELECT quantity FROM allocations WHERE active = 1").fetchall()
    assert [a["quantity"] for a in active] == ["40"]  # no double allocation
    restarted.close()


def test_at21_receipt_with_three_way_disabled(conn, run) -> None:
    out = orchestrator.process(conn, fx("inv_AT01.json"), fx("po_AT01.json"),
                          receipt_src=fx("receipt_AT21.json"), reference_dir=REFERENCE)
    assert out.result_state == "MATCHED_2WAY"
    assert out.result_state != "MATCHED_3WAY"
    assert findings(conn, out.case_id)["receipt"]["check_status"] == "CHECK_NOT_EVALUATED"
    stored = conn.execute("SELECT validation_status FROM ingested_documents WHERE doc_type='861'"
                          ).fetchone()
    assert stored["validation_status"] == "STORED_UNSUPPORTED"
    assert "unsupported for reconciliation" in out.message


def test_at22_rerun_invalidates_stale_suggestion(conn, run) -> None:
    out = run("inv_AT04.json", "po_AT04.json")
    expl, rec_v1 = orchestrator.request_ai_explanation(conn, out.case_id)
    assert rec_v1 is not None
    state = orchestrator.rerun_case(conn, out.case_id, fx("inv_AT22_corrected.json"),
                               reference_dir=REFERENCE)
    assert state == "MATCHED_2WAY"
    c = case(conn, out.case_id)
    with pytest.raises(StaleVersionError):
        review(conn, out.case_id, "approve", "apply AI", c["version"], "at22",
                              recommendation_version=rec_v1)
    assert findings(conn, out.case_id, 1)["unit_price"]["delta"] == "+0.10"  # history kept
    assert orchestrator.get_cached_explanation(conn, out.case_id, 1) is not None
    assert "RERUN_VERSION_SUPERSEDED" in events(conn, out.case_id)
    docs = conn.execute("SELECT validation_status FROM ingested_documents WHERE doc_type='810'"
                        " ORDER BY ingested_at").fetchall()
    assert [d["validation_status"] for d in docs] == ["VALID", "VALID_REVISION"]


def test_at23_no_cross_partner_link(conn, run) -> None:
    run("inv_AT01.json", "po_AT23.json")  # stores PO-AT23 for SupplierInc
    out = run("inv_AT23_other_partner.json")
    assert out.result_state == "WAIT_DOCUMENT"
    assert case(conn, out.case_id)["po_id"] is None
    assert "OtherSupplierLLC" in result(conn, out.case_id)["missing"]


def test_at24_missing_basis_totals_and_contract_conflict(conn, run) -> None:
    out = run("inv_AT24.json", "po_AT24.json")
    f = findings(conn, out.case_id)
    assert out.result_state == "INSUFFICIENT_EVIDENCE"
    assert f["price_basis"]["finding_type"] == "INSUFFICIENT_EVIDENCE"
    assert f["unit_price"]["check_status"] == "CHECK_NOT_EVALUATED"
    assert f["invoice_total"]["check_status"] == "CHECK_FAILED"
    assert f["invoice_total"]["delta"] == "+50.00"
    assert "Contradictory contract evidence" in f["contract_reference"]["message"]
