"""
tests/test_models.py

Phase 1 unit tests — 22 tests across 6 groups.
All tests must pass before Phase 1 is considered complete.

Groups:
  A — Money construction, arithmetic, and rejection of float/int
  B — FindingStatus enum correctness
  C — ReviewRequest validators (reason, escalation_owner)
  D — CaseResult version field
  E — Settings defaults and env file loading
  F — Model round-trip JSON serialization
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.models import (
    CaseResult,
    FindingStatus,
    Invoice,
    InvoiceLine,
    LineResult,
    Money,
    POLine,
    PurchaseOrder,
    ReviewActionType,
    ReviewRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_po_line(**overrides) -> POLine:
    defaults = dict(
        line_id="1",
        product_id="SKU-ABC",
        ordered_qty=Decimal("100"),
        uom="EA",
        unit_price=Money(amount=Decimal("2.00")),
        price_basis=1,
        currency="USD",
    )
    defaults.update(overrides)
    return POLine(**defaults)


def _make_po(**overrides) -> PurchaseOrder:
    defaults = dict(
        po_id="PO-12345",
        order_date=date(2026, 10, 1),
        buyer_id="BUYER-001",
        supplier_id="SUP-001",
        currency="USD",
        lines=[_make_po_line()],
        content_hash="sha256:abc123",
    )
    defaults.update(overrides)
    return PurchaseOrder(**defaults)


def _make_invoice_line(**overrides) -> InvoiceLine:
    defaults = dict(
        line_id="1",
        po_reference="PO-12345",
        line_reference="1",
        product_id="SKU-ABC",
        invoiced_qty=Decimal("100"),
        uom="EA",
        unit_price=Money(amount=Decimal("2.00")),
        price_basis=1,
        currency="USD",
    )
    defaults.update(overrides)
    return InvoiceLine(**defaults)


def _make_invoice(**overrides) -> Invoice:
    defaults = dict(
        invoice_id="INV-001",
        invoice_date=date(2026, 10, 5),
        po_references=["PO-12345"],
        buyer_id="BUYER-001",
        supplier_id="SUP-001",
        currency="USD",
        lines=[_make_invoice_line()],
        content_hash="sha256:def456",
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def _make_line_result(**overrides) -> LineResult:
    defaults = dict(
        line_id="lr-1",
        po_line_id="1",
        invoice_line_id="1",
        status=FindingStatus.MATCHED_2WAY,
        allocated_qty=Decimal("100"),
    )
    defaults.update(overrides)
    return LineResult(**defaults)


def _make_case_result(**overrides) -> CaseResult:
    defaults = dict(
        case_id="case-uuid-001",
        run_id="run-uuid-001",
        case_version=1,
        po_id="PO-12345",
        invoice_id="INV-001",
        overall_status=FindingStatus.MATCHED_2WAY,
        line_results=[_make_line_result()],
        policy_version="v1",
        run_timestamp=datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return CaseResult(**defaults)


# ---------------------------------------------------------------------------
# Group A — Money
# ---------------------------------------------------------------------------


def test_money_accepts_decimal():
    m = Money(amount=Decimal("2.00"))
    assert m.amount == Decimal("2.00")


def test_money_accepts_string():
    m = Money.from_str("2.00")
    assert m.amount == Decimal("2.00")


def test_money_rejects_float():
    with pytest.raises(ValidationError) as exc_info:
        Money(amount=2.0)
    assert "float" in str(exc_info.value).lower()


def test_money_rejects_int():
    with pytest.raises(ValidationError) as exc_info:
        Money(amount=2)
    assert "int" in str(exc_info.value).lower()


def test_money_arithmetic_stays_decimal():
    result = Money.from_str("1.00") + Money.from_str("0.10")
    assert isinstance(result, Money)
    assert result.amount == Decimal("1.10")
    assert not isinstance(result.amount, float)


def test_money_from_str_rounds():
    # 2.335 rounds to 2.34 with ROUND_HALF_UP
    m = Money.from_str("2.335")
    assert m.amount == Decimal("2.34")


def test_money_equality():
    assert Money.from_str("2.00") == Money.from_str("2.00")
    assert Money.from_str("2.00") != Money.from_str("2.01")


# ---------------------------------------------------------------------------
# Group B — FindingStatus
# ---------------------------------------------------------------------------


def test_not_evaluated_distinct_from_check_failed():
    assert FindingStatus.NOT_EVALUATED != FindingStatus.CHECK_FAILED


def test_all_status_values_are_strings():
    for member in FindingStatus:
        assert isinstance(member.value, str), f"{member} value is not a str"


def test_finding_status_serializes_to_string():
    # json.dumps must not raise and must produce the string value
    payload = {"s": FindingStatus.MATCHED_2WAY}
    serialized = json.dumps(payload)
    parsed = json.loads(serialized)
    assert parsed["s"] == "MATCHED_2WAY"


# ---------------------------------------------------------------------------
# Group C — ReviewRequest validators
# ---------------------------------------------------------------------------


def test_review_request_requires_reason():
    with pytest.raises(ValidationError):
        ReviewRequest(
            case_id="case-1",
            case_version=1,
            action=ReviewActionType.APPROVE,
            reason="",
        )


def test_review_request_rejects_whitespace_only_reason():
    with pytest.raises(ValidationError):
        ReviewRequest(
            case_id="case-1",
            case_version=1,
            action=ReviewActionType.APPROVE,
            reason="   ",
        )


def test_escalate_requires_owner():
    with pytest.raises(ValidationError) as exc_info:
        ReviewRequest(
            case_id="case-1",
            case_version=1,
            action=ReviewActionType.ESCALATE,
            reason="Needs pricing team review",
            escalation_owner=None,
        )
    assert "escalation_owner" in str(exc_info.value).lower()


def test_escalate_with_owner_valid():
    req = ReviewRequest(
        case_id="case-1",
        case_version=1,
        action=ReviewActionType.ESCALATE,
        reason="Needs pricing team review",
        escalation_owner="ops_manager",
    )
    assert req.escalation_owner == "ops_manager"


def test_approve_does_not_require_owner():
    req = ReviewRequest(
        case_id="case-1",
        case_version=1,
        action=ReviewActionType.APPROVE,
        reason="Prices verified against contract.",
        escalation_owner=None,
    )
    assert req.action == ReviewActionType.APPROVE


# ---------------------------------------------------------------------------
# Group D — CaseResult version field
# ---------------------------------------------------------------------------


def test_case_result_version_is_int():
    cr = _make_case_result(case_version=1)
    assert isinstance(cr.case_version, int)
    assert cr.case_version == 1


def test_run_id_distinct_from_case_id():
    cr = _make_case_result(case_id="case-aaa", run_id="run-bbb")
    assert cr.case_id == "case-aaa"
    assert cr.run_id == "run-bbb"
    assert cr.case_id != cr.run_id


# ---------------------------------------------------------------------------
# Group E — Settings
# ---------------------------------------------------------------------------


def test_settings_defaults(tmp_path, monkeypatch):
    # Ensure no .env file interferes by pointing settings at a nonexistent path
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=str(tmp_path / ".env.nonexistent"))
    assert s.ai_provider == "mock"
    assert s.three_way_enabled is False
    assert s.max_retry_attempts == 3


def test_settings_loads_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("AI_PROVIDER=openai\n")
    s = Settings(_env_file=str(env_file))
    assert s.ai_provider == "openai"


def test_three_way_enabled_default_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=str(tmp_path / ".env.nonexistent"))
    assert s.three_way_enabled is False


# ---------------------------------------------------------------------------
# Group F — Model round-trip JSON serialization
# ---------------------------------------------------------------------------


def test_purchase_order_roundtrip():
    po = _make_po()
    json_str = po.model_dump_json()
    po2 = PurchaseOrder.model_validate_json(json_str)
    assert po2.po_id == po.po_id
    assert po2.order_date == po.order_date
    assert po2.lines[0].unit_price == po.lines[0].unit_price
    assert po2.lines[0].ordered_qty == po.lines[0].ordered_qty


def test_invoice_roundtrip():
    inv = _make_invoice()
    json_str = inv.model_dump_json()
    inv2 = Invoice.model_validate_json(json_str)
    assert inv2.invoice_id == inv.invoice_id
    assert inv2.lines[0].unit_price == inv.lines[0].unit_price
    assert inv2.lines[0].invoiced_qty == inv.lines[0].invoiced_qty


def test_money_survives_json_roundtrip():
    po = _make_po()
    json_str = po.model_dump_json()
    data = json.loads(json_str)

    # Money should serialize to a string, never a bare float
    line_data = data["lines"][0]
    assert isinstance(line_data["unit_price"], str), (
        f"Money serialized as {type(line_data['unit_price']).__name__}, expected str"
    )

    # Deserialize back and confirm Decimal equality
    po2 = PurchaseOrder.model_validate_json(json_str)
    assert po2.lines[0].unit_price.amount == Decimal("2.00")
    assert not isinstance(po2.lines[0].unit_price.amount, float)
