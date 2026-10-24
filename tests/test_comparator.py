from decimal import Decimal

from backend.comparator import compare, round_money
from backend.models import CheckStatus, FindingStatus, Money, ReconciliationPolicy
from backend.parser import parse_invoice, parse_po
from backend.reference import load_price_contracts, load_uom_conversions
from tests.conftest import REFERENCE, fx

UOM = load_uom_conversions(REFERENCE / "uom_conversions.json")
PRICES = load_price_contracts(REFERENCE / "price_contracts.json")


def run(po: str, inv: str, **kwargs):
    return compare(parse_po(fx(po)), parse_invoice(fx(inv)), UOM, PRICES, **kwargs)


def finding(result, check: str):
    return next(f for f in result.all_findings() if f.check == check)


def test_round_money_half_up() -> None:
    assert round_money(Decimal("1.005")) == Decimal("1.01")
    assert round_money(Decimal("2.345")) == Decimal("2.35")


def test_at01_match() -> None:
    r = run("po_AT01.json", "inv_AT01.json")
    assert r.overall_status == FindingStatus.MATCHED_2WAY
    assert finding(r, "receipt").status == CheckStatus.CHECK_NOT_EVALUATED


def test_at02_uom_normalization() -> None:
    r = run("po_AT02.json", "inv_AT02.json")
    rec = r.line_results[0]
    assert r.overall_status == FindingStatus.MATCHED_2WAY
    assert rec.po_value_normalized["quantity"] == rec.invoice_value_normalized["quantity"] == "600"
    assert Decimal(rec.po_value_normalized["unit_price"]) == Decimal("2")
    assert rec.invoice_value_normalized["extension"] == "1200.00"


def test_at03_missing_conversion() -> None:
    r = run("po_AT03.json", "inv_AT03.json")
    assert r.overall_status == FindingStatus.INSUFFICIENT_EVIDENCE
    for check in ("quantity", "unit_price", "extension_vs_po"):
        assert finding(r, check).status == CheckStatus.CHECK_NOT_EVALUATED


def test_at04_price_variance() -> None:
    r = run("po_AT04.json", "inv_AT04.json")
    assert r.overall_status == FindingStatus.PRICE_VARIANCE
    assert finding(r, "unit_price").delta == "+0.10"
    assert finding(r, "unit_price").status == CheckStatus.CHECK_FAILED
    assert finding(r, "extension_vs_po").delta == "+10.00"
    assert {"po.1.unit_price", "inv.1.unit_price"} <= set(finding(r, "unit_price").evidence_ids)


def test_at05_contract_displayed_separately() -> None:
    r = run("po_AT05.json", "inv_AT05.json")
    assert r.overall_status == FindingStatus.PRICE_VARIANCE
    assert "contract.CTR-005" in r.evidence_map()
    assert Decimal(finding(r, "unit_price").normalized_po_value) == Decimal("2.00")


def test_at07_partial_billing() -> None:
    r = run("po_AT07.json", "inv_AT07.json")
    assert r.overall_status == FindingStatus.PARTIAL_BILLING
    assert r.line_results[0].delta["po_balance"] == "60"
    assert r.allocations == [{"po_line": "1", "quantity": "40", "uom": "EA"}]


def test_at08_cumulative_quantity_variance() -> None:
    r = run("po_AT08.json", "inv_AT08b.json", prior_allocated={"1": Decimal("70")})
    q = finding(r, "quantity")
    assert r.overall_status == FindingStatus.QUANTITY_VARIANCE
    assert q.normalized_invoice_value == "110"
    assert q.delta == "+10"
    assert r.allocations == []


def test_at12_currency_held() -> None:
    r = run("po_AT12.json", "inv_AT12_currency.json")
    assert r.overall_status == FindingStatus.INSUFFICIENT_EVIDENCE
    assert finding(r, "currency").status == CheckStatus.CHECK_NOT_EVALUATED
    assert finding(r, "extension_vs_po").status == CheckStatus.CHECK_NOT_EVALUATED


def test_at13_precision() -> None:
    r = run("po_AT13a.json", "inv_AT13a.json")
    assert r.line_results[0].po_value_normalized["extension"] == "1.01"
    r2 = run("po_AT13b.json", "inv_AT13b.json")
    assert Decimal(r2.line_results[0].po_value_normalized["unit_price"]) == Decimal("0.10")
    assert r2.line_results[0].po_value_normalized["extension"] == "2.00"


def test_at14_tolerance_boundary() -> None:
    policy = ReconciliationPolicy.baseline().model_copy(update={
        "price_abs_tolerance": Money(amount=Decimal("0.01")), "policy_version": "AT14-abs-0.01"})
    ok = run("po_AT14.json", "inv_AT14a.json", policy=policy)
    bad = run("po_AT14.json", "inv_AT14b.json", policy=policy)
    assert finding(ok, "unit_price").status == CheckStatus.CHECK_PASSED
    assert finding(ok, "unit_price").delta == "+0.01"
    assert finding(bad, "unit_price").status == CheckStatus.CHECK_FAILED
    assert finding(bad, "unit_price").delta == "+0.0101"
    assert "AT14-abs-0.01" in finding(bad, "unit_price").tolerance_applied


def test_zero_tolerance_default() -> None:
    r = run("po_AT14.json", "inv_AT14a.json")
    assert finding(r, "unit_price").status == CheckStatus.CHECK_FAILED


def test_not_evaluated_distinct_from_failed() -> None:
    r = run("po_AT03.json", "inv_AT03.json")
    statuses = {f.status for f in r.all_findings()}
    assert CheckStatus.CHECK_FAILED not in statuses
    assert CheckStatus.CHECK_NOT_EVALUATED in statuses
