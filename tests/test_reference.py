from datetime import date
from decimal import Decimal

import pytest

from backend.exceptions import ReconciliationError
from backend.models import Money, PriceContract, UOMConversion
from backend.reference import (
    get_contract_price,
    get_conversion,
    load_price_contracts,
    load_product_master,
    load_uom_conversions,
    reference_version,
)
from tests.conftest import REFERENCE

ON = date(2026, 3, 1)


def conv(factor: str, product: str | None = "P", **kw) -> UOMConversion:
    return UOMConversion(from_uom="CS", to_uom="EA", factor=Decimal(factor), product_id=product,
                         effective_date=kw.get("start", date(2026, 1, 1)),
                         expiry_date=kw.get("end"), source="test")


def contract(cid: str, price: str, start: date, end: date | None, scope: str | None = None):
    return PriceContract(contract_id=cid, product_id="P", agreed_price=Money(amount=Decimal(price)),
                         price_basis=1, currency="USD", effective_from=start, effective_to=end,
                         partner_scope=scope, policy_version="t")


def test_valid_load() -> None:
    uom = load_uom_conversions(REFERENCE / "uom_conversions.json")
    contracts = load_price_contracts(REFERENCE / "price_contracts.json")
    assert get_conversion("PROD-CS-001", "CS", "EA", uom, ON) == Decimal("12")
    assert get_conversion("PROD-CS-001", "EA", "CS", uom, ON) == Decimal("1") / Decimal("12")
    found = get_contract_price("PROD-EA-005", ON, contracts, "SupplierInc", "USD")
    assert found.agreed_price.amount == Decimal("2.10")


def test_missing_file_is_empty(tmp_path) -> None:
    assert load_uom_conversions(tmp_path / "nope.json") == []
    assert load_price_contracts(tmp_path / "nope.json") == []
    assert load_product_master(tmp_path / "nope.json") == {}


def test_float_numbers_rejected(tmp_path) -> None:
    path = tmp_path / "uom.json"
    path.write_text('[{"from_uom": "CS", "to_uom": "EA", "factor": 12.0}]')
    with pytest.raises(ReconciliationError, match="JSON strings"):
        load_uom_conversions(path)


def test_missing_conversion_returns_none() -> None:
    uom = load_uom_conversions(REFERENCE / "uom_conversions.json")
    assert get_conversion("PROD-CS-002", "CS", "EA", uom, ON) is None


def test_universal_conversion_fallback() -> None:
    assert get_conversion("ANY", "CS", "EA", [conv("24", product=None)], ON) == Decimal("24")
    both = [conv("24", product=None), conv("12", product="ANY")]
    assert get_conversion("ANY", "CS", "EA", both, ON) == Decimal("12")  # specific wins


def test_expired_contract_returns_none() -> None:
    old = [contract("C1", "1.00", date(2025, 1, 1), date(2025, 12, 31))]
    assert get_contract_price("P", ON, old) is None


def test_contract_partner_scope() -> None:
    scoped = [contract("C1", "1.00", date(2026, 1, 1), None, scope="SupplierInc")]
    assert get_contract_price("P", ON, scoped, "OtherSupplier", "USD") is None
    assert get_contract_price("P", ON, scoped, "SupplierInc", "USD").contract_id == "C1"
    assert get_contract_price("P", ON, scoped, "SupplierInc", "EUR") is None


def test_expired_conversion_returns_none() -> None:
    assert get_conversion("P", "CS", "EA", [conv("12", end=date(2025, 12, 31),
                                                  start=date(2025, 1, 1))], ON) is None


def test_overlapping_contracts_raise() -> None:
    contracts = load_price_contracts(REFERENCE / "price_contracts.json")
    with pytest.raises(ReconciliationError, match="overlapping"):
        get_contract_price("PROD-EA-024", ON, contracts, "SupplierInc", "USD")


def test_conflicting_conversions_raise() -> None:
    with pytest.raises(ReconciliationError, match="conflicting"):
        get_conversion("P", "CS", "EA", [conv("12"), conv("10")], ON)


def test_reference_version_stable() -> None:
    assert reference_version(REFERENCE) == reference_version(REFERENCE)
