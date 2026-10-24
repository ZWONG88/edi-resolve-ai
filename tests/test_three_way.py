"""Three-way matching (PRD AT25-AT35) and the team rules doc, end to end.

AT33 (verified receipt correction replacing an earlier quantity) and AT36 (concurrent
approvals racing for the same receipt) are not implemented yet; see docs/test_results.md.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from backend import db, orchestrator
from backend.models import ReviewActionType, ReviewRequest
from tests import team_docs as t
from tests.conftest import REFERENCE


@pytest.fixture(autouse=True)
def _three_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")


def run(conn, inv: bytes, po: bytes | None = None, rcv: bytes | None = None):
    return orchestrator.process(conn, inv, po, rcv, reference_dir=REFERENCE)


def case(conn, case_id: str):
    return db.get_case(conn, case_id)


def result(conn, case_id: str) -> dict:
    return json.loads(case(conn, case_id)["result_json"])


def checks(conn, case_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in db.list_findings(conn, case_id):
        out.setdefault(row["check_name"], []).append(dict(row))
    return out


def receipt_states(conn, case_id: str) -> set[str]:
    return {f["finding_type"] for f in checks(conn, case_id).get("receipt", [])
            if f["finding_type"]}


PO_100 = t.po([(1, "SKU-A", "100", "EA", "2.00")])


def inv_qty(qty: str, number: str = "INV-T1", price: str = "2.00") -> bytes:
    total = str((Decimal(qty) * Decimal(price)).quantize(Decimal("0.01")))
    return t.invoice([(1, "SKU-A", qty, "EA", price, total)], number=number)


# ------------------------------------------------------------ PRD AT25-AT35


def test_at25_three_way_match(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100, t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["status"] == "MATCHED_3WAY" and c["mode"] == "3way"
    assert c["disposition_status"] == "AUTO_APPROVED" and c["review_status"] == "AUTO_APPROVED"
    rows = conn.execute("SELECT quantity FROM receipt_allocations WHERE active = 1").fetchall()
    assert [r["quantity"] for r in rows] == ["100"]
    remit = orchestrator.current_remittance(conn, out.case_id)
    assert remit["paid_amount"] == "200.00" and Path(remit["file_path"]).exists()


def test_at26_wait_receipt_no_two_way_fallback(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100)
    c = case(conn, out.case_id)
    assert c["status"] == "WAIT_RECEIPT" and c["disposition_status"] == "HOLD"
    assert c["review_status"] == "OPEN"
    assert orchestrator.current_remittance(conn, out.case_id) is None


def test_at27_accepted_not_received(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100, t.receipt([(1, "SKU-A", "100", "90", "10", "EA")]))
    c = case(conn, out.case_id)
    assert {"RECEIPT_VARIANCE", "RECEIPT_REVIEW"} <= receipt_states(conn, out.case_id)
    assert c["disposition_status"] == "NEEDS_HUMAN_AP_REVIEW"  # never a pass
    assert Decimal(c["blocked_amount"]) == Decimal("20.00")  # 10 short x $2.00
    assert orchestrator.current_remittance(conn, out.case_id) is None


def test_at28_partial_three_way(conn) -> None:
    out = run(conn, inv_qty("40"), PO_100, t.receipt([(1, "SKU-A", "40", "40", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["status"] == "PARTIAL_BILLING" and c["disposition_status"] == "AUTO_APPROVED"
    assert result(conn, out.case_id)["line_results"][0]["delta"]["po_balance"] == "60"


def test_at29_incremental_events_and_replay(conn) -> None:
    first = t.receipt([(1, "SKU-A", "40", "40", "0", "EA")], number="RA-1",
                      mode="incremental", event="E1")
    second = t.receipt([(1, "SKU-A", "60", "60", "0", "EA")], number="RA-2",
                       mode="incremental", event="E2", date="2026-10-16")
    orchestrator.process(conn, inv_qty("1", number="INV-SEED"), PO_100, first,
                         reference_dir=REFERENCE)
    orchestrator.ingest_receipt(conn, second)
    assert "replay" in orchestrator.ingest_receipt(conn, second)
    out = run(conn, inv_qty("99", number="INV-T2"))
    line = result(conn, out.case_id)["line_results"][0]
    assert Decimal(line["accepted_qty"]) == Decimal("99")  # 100 accepted - 1 already billed
    assert conn.execute("SELECT COUNT(*) FROM ingested_documents WHERE doc_type='861'"
                        " AND validation_status='VALID'").fetchone()[0] == 2


def test_at30_cumulative_snapshots(conn) -> None:
    snap1 = t.receipt([(1, "SKU-A", "40", "40", "0", "EA")], number="RA-1", mode="cumulative")
    snap2 = t.receipt([(1, "SKU-A", "100", "100", "0", "EA")], number="RA-2",
                      mode="cumulative", date="2026-10-16")
    orchestrator.ingest_receipt(conn, snap1)
    out = run(conn, inv_qty("100"), PO_100, snap2)
    c = case(conn, out.case_id)
    assert c["status"] == "MATCHED_3WAY"  # 100 accepted, not 140
    assert Decimal(result(conn, out.case_id)["line_results"][0]["accepted_qty"]) == 100


def test_at31_receipt_balance_across_invoices(conn) -> None:
    po = t.po([(1, "SKU-A", "200", "EA", "2.00")])
    rcv = t.receipt([(1, "SKU-A", "100", "100", "0", "EA")])
    a = run(conn, inv_qty("60", number="INV-A"), po, rcv)
    b = run(conn, inv_qty("50", number="INV-B"))
    assert case(conn, a.case_id)["status"] == "PARTIAL_BILLING"
    shortage = [f for f in checks(conn, b.case_id)["receipt"]
                if f["finding_type"] == "RECEIPT_VARIANCE"]
    assert shortage and shortage[0]["delta"] == "+10"
    total = sum(Decimal(r["quantity"]) for r in conn.execute(
        "SELECT quantity FROM receipt_allocations WHERE active = 1"))
    assert total == Decimal("100")  # never more than accepted


def test_at32_over_receipt_does_not_authorize_billing(conn) -> None:
    out = run(conn, inv_qty("110"), PO_100, t.receipt([(1, "SKU-A", "110", "110", "0", "EA")]))
    states = {f["finding_type"] for rows in checks(conn, out.case_id).values() for f in rows}
    assert {"OVER_RECEIPT", "QUANTITY_VARIANCE"} <= states
    c = case(conn, out.case_id)
    assert c["disposition_status"] == "NEEDS_HUMAN_AP_REVIEW"
    assert Decimal(c["blocked_amount"]) == Decimal("20.00")  # 10 over the PO x $2.00


def test_at34_unlinked_or_ambiguous_receipt(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100, t.receipt([(1, "SKU-OTHER", "100", "100", "0",
                                                         "EA")]))
    assert "REVIEW_LINK" in receipt_states(conn, out.case_id)
    assert case(conn, out.case_id)["disposition_status"] == "HOLD"

    other = db.init_db(Path(str(conn.execute("PRAGMA database_list").fetchone()[2])
                            + ".2"))
    run(other, inv_qty("1", number="INV-X"), PO_100,
        t.receipt([(1, "SKU-A", "50", "50", "0", "EA")], number="RA-1"))
    orchestrator.ingest_receipt(other, t.receipt([(1, "SKU-A", "50", "50", "0", "EA")],
                                                 number="RA-2", date="2026-10-16"))
    out2 = run(other, inv_qty("90", number="INV-Y"))
    assert "INSUFFICIENT_EVIDENCE" in receipt_states(other, out2.case_id)  # no semantics
    other.close()


def test_at35_receipt_arrives_later(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100)
    assert case(conn, out.case_id)["status"] == "WAIT_RECEIPT"
    note = orchestrator.ingest_receipt(conn, t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    assert "reran 1 case" in note
    c = case(conn, out.case_id)
    assert c["status"] == "MATCHED_3WAY" and c["evidence_version"] == 2
    history = [e["before_state"] for e in db.list_audit_events(conn, out.case_id)
               if e["event_type"] == "RERUN_VERSION_SUPERSEDED"]
    assert history == ["WAIT_RECEIPT"]
    assert "replay" in orchestrator.ingest_receipt(
        conn, t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))


# ------------------------------------------------------------ team rules doc


def test_rule_3_1_unmapped_ghost_line(conn) -> None:
    inv = t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00"),
                     (2, "SKU-GHOST", "5", "EA", "3.00", "15.00")])
    rcv = t.receipt([(1, "SKU-A", "100", "100", "0", "EA")])
    out = run(conn, inv, PO_100, rcv)
    ghost = result(conn, out.case_id)["line_results"][1]
    assert ghost["status"] == "UNMAPPED_LINE_ITEM"
    assert ghost["approved_amount"] == "0" and ghost["adjustments"] == {"ADJ_UNMAPPED": "15.00"}
    c = case(conn, out.case_id)
    assert c["disposition_status"] == "APPROVED_WITH_SHORT_PAY"
    remit = json.loads(orchestrator.current_remittance(conn, out.case_id)["payload_json"])
    assert remit["paid_amount"] == "200.00" and remit["adjustment_amount"] == "15.00"


def test_rule_2_2_composite_key_line_number_and_sku(conn) -> None:
    po = t.po([(1, "SKU-A", "10", "EA", "2.00"), (2, "SKU-B", "10", "EA", "3.00")])
    swapped = t.invoice([(1, "SKU-B", "10", "EA", "3.00", "30.00")])  # line 1 is SKU-A
    out = run(conn, swapped, po, t.receipt([(1, "SKU-A", "10", "10", "0", "EA"),
                                            (2, "SKU-B", "10", "10", "0", "EA")]))
    assert result(conn, out.case_id)["line_results"][0]["status"] == "UNMAPPED_LINE_ITEM"


def test_rule_3_2_price_overcharge_short_pay(conn) -> None:
    out = run(conn, inv_qty("100", price="2.10"), PO_100,
              t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["status"] == "PRICE_VARIANCE"
    assert c["disposition_status"] == "APPROVED_WITH_SHORT_PAY"
    assert (c["approved_amount"], c["blocked_amount"]) == ("200.00", "10.00")
    line = result(conn, out.case_id)["line_results"][0]
    assert line["adjustments"] == {"ADJ_PRICE": "10.00"}
    assert "PO price $2.00/EA" in line["correction"]


def test_rule_3_2_undercharge_is_informational(conn) -> None:
    out = run(conn, inv_qty("100", price="1.90"), PO_100,
              t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["status"] == "MATCHED_3WAY" and c["disposition_status"] == "AUTO_APPROVED"
    assert c["approved_amount"] == "190.00"


def test_rule_4_1_de_minimis(conn) -> None:
    out = run(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.01", "201.00")]), PO_100,
              t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["status"] == "PRICE_VARIANCE"  # variance still recorded
    assert c["disposition_status"] == "AUTO_APPROVED" and Decimal(c["blocked_amount"]) == 0
    assert result(conn, out.case_id)["line_results"][0]["de_minimis"] is True


def test_rule_4_1_header_de_minimis(conn) -> None:
    rcv = t.receipt([(1, "SKU-A", "100", "100", "0", "EA")])
    out = run(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")], total="201.00"),
              PO_100, rcv)
    c = case(conn, out.case_id)
    assert c["status"] == "PRICE_VARIANCE"  # header variance still recorded
    assert c["disposition_status"] == "AUTO_APPROVED"
    assert (c["approved_amount"], Decimal(c["blocked_amount"])) == ("201.00", 0)
    remit = json.loads(orchestrator.current_remittance(conn, out.case_id)["payload_json"])
    assert (remit["paid_amount"], remit["header_waived"]) == ("201.00", "1.00")

    over = run(conn, t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")], number="INV-T2",
                               po_number="PO-T2", total="201.01"),
               t.po([(1, "SKU-A", "100", "EA", "2.00")], number="PO-T2"),
               t.receipt([(1, "SKU-A", "100", "100", "0", "EA")], number="RA-T2",
                         po_number="PO-T2"))
    c2 = case(conn, over.case_id)
    assert c2["disposition_status"] == "APPROVED_WITH_SHORT_PAY"
    assert c2["blocked_amount"] == "1.01"


def test_rule_4_2_high_value_hold_then_approval_generates_820(conn) -> None:
    po = t.po([(1, "SKU-A", "1000", "EA", "2.00")])
    out = run(conn, t.invoice([(1, "SKU-A", "1000", "EA", "2.60", "2600.00")]), po,
              t.receipt([(1, "SKU-A", "1000", "1000", "0", "EA")]))
    c = case(conn, out.case_id)
    assert c["disposition_status"] == "NEEDS_HUMAN_AP_REVIEW" and c["blocked_amount"] == "600.00"
    assert c["review_status"] == "OPEN"
    assert orchestrator.current_remittance(conn, out.case_id) is None
    orchestrator.review_action(conn, ReviewRequest(
        case_id=out.case_id, case_version=c["version"], action=ReviewActionType.APPROVE,
        reason="Short pay at PO price", idempotency_key="hv-1"))
    remit = orchestrator.current_remittance(conn, out.case_id)
    assert remit["paid_amount"] == "2000.00"


def test_rule_1_2_uom_normalization(conn) -> None:
    po = t.po([(1, "PROD-CS-001", "5", "CS", "24.00")])
    out = run(conn, t.invoice([(1, "PROD-CS-001", "60", "EA", "2.00", "120.00")]), po,
              t.receipt([(1, "PROD-CS-001", "5", "5", "0", "CS")]))
    c = case(conn, out.case_id)
    assert c["status"] == "MATCHED_3WAY" and c["disposition_status"] == "AUTO_APPROVED"
    assert Decimal(result(conn, out.case_id)["line_results"][0]["accepted_qty"]) == 60


def test_rerun_supersedes_820(conn) -> None:
    out = run(conn, inv_qty("100"), PO_100, t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    orchestrator.rerun_case(conn, out.case_id, reference_dir=REFERENCE)
    statuses = [r["status"] for r in conn.execute(
        "SELECT status FROM remittances ORDER BY created_at")]
    assert statuses == ["SUPERSEDED", "GENERATED_NOT_TRANSMITTED"]
