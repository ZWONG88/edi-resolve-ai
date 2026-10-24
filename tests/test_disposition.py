"""Dollar impact and disposition (team rules doc Stages 3-5)."""

from decimal import Decimal

from backend.disposition import classify, line_impact, unmapped_impact
from backend.models import Disposition, FindingStatus

D = Decimal


def impact(**kw):
    base = dict(billed_qty=D("10"), inv_price=D("2.00"), po_price=D("2.00"),
                invoiced_amount=D("20.00"), po_remaining=D("10"), accepted_available=D("10"),
                uom="EA", de_minimis=D("1.00"))
    return line_impact(**{**base, **kw})


def test_clean_line() -> None:
    r = impact()
    assert r["blocked_amount"].amount == 0 and r["approved_amount"].amount == D("20.00")
    assert r["correction"] is None


def test_price_risk_matches_doc_formula() -> None:
    r = impact(inv_price=D("2.50"), invoiced_amount=D("25.00"))
    assert r["blocked_amount"].amount == D("5.00")  # (2.50 - 2.00) x 10
    assert r["adjustments"] == {"ADJ_PRICE": "5.00"}
    assert "PO price $2.00/EA" in r["correction"]


def test_shortage_risk_matches_doc_formula() -> None:
    r = impact(accepted_available=D("7"))
    assert r["blocked_amount"].amount == D("6.00")  # (10 - 7) x PO price 2.00
    assert r["adjustments"] == {"ADJ_SHORTAGE": "6.00"}
    assert "7 EA accepted at receiving" in r["correction"]


def test_price_and_shortage_not_double_counted() -> None:
    r = impact(inv_price=D("2.50"), invoiced_amount=D("25.00"), accepted_available=D("7"))
    assert r["approved_amount"].amount == D("14.00")  # 7 x 2.00
    assert r["blocked_amount"].amount == D("11.00")  # price 5.00 + shortage 6.00


def test_undercharge_not_blocked() -> None:
    r = impact(inv_price=D("1.90"), invoiced_amount=D("19.00"))
    assert r["blocked_amount"].amount == 0 and r["approved_amount"].amount == D("19.00")


def test_de_minimis_boundary() -> None:
    waived = impact(inv_price=D("2.10"), invoiced_amount=D("21.00"))  # $1.00 variance
    kept = impact(inv_price=D("2.101"), invoiced_amount=D("21.01"))  # $1.01 variance
    assert waived["de_minimis"] and waived["blocked_amount"].amount == 0
    assert waived["waived_amount"].amount == D("1.00")  # actual variance retained
    assert not kept["de_minimis"] and kept["blocked_amount"].amount == D("1.01")


def test_unmapped_blocks_everything() -> None:
    r = unmapped_impact(D("226.40"), "18", "SKU2018X")
    assert r["approved_amount"].amount == 0 and r["blocked_amount"].amount == D("226.40")
    assert r["adjustments"] == {"ADJ_UNMAPPED": "226.40"}


def test_classify_thresholds() -> None:
    hv = D("500.00")
    assert classify(FindingStatus.MATCHED_3WAY, "3way", D("0"), hv) == Disposition.AUTO_APPROVED
    assert classify(FindingStatus.PRICE_VARIANCE, "3way", D("500.00"), hv) == \
        Disposition.APPROVED_WITH_SHORT_PAY
    assert classify(FindingStatus.PRICE_VARIANCE, "3way", D("500.01"), hv) == \
        Disposition.NEEDS_HUMAN_AP_REVIEW
    assert classify(FindingStatus.RECEIPT_REVIEW, "3way", D("0"), hv) == \
        Disposition.NEEDS_HUMAN_AP_REVIEW
    assert classify(FindingStatus.WAIT_RECEIPT, "3way", None, hv) == Disposition.HOLD
    assert classify(FindingStatus.WAIT_DOCUMENT, "3way", None, hv) == \
        Disposition.PO_HEADER_MISMATCH
    assert classify(FindingStatus.MATCHED_2WAY, "2way", D("0"), hv) == \
        Disposition.RECEIPT_NOT_VERIFIED
