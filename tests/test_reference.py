from decimal import Decimal

import pytest

from backend.exceptions import ReconciliationError
from backend.models import PriceContract, UOMConversion
from backend.reference import (
    get_contract_price,
    get_conversion,
    load_price_contracts,
    load_uom_conversions,
    reference_version,
)
from tests.conftest import REFERENCE


def test_valid_load() -> None:
    uom = load_uom_conversions(REFERENCE / "uom_conversions.json")
    contracts = load_price_contracts(REFERENCE / "price_contracts.json")
    assert get_conversion("PROD-CS-001", "CS", "EA", uom) == Decimal("12")
    assert get_conversion("PROD-CS-001", "EA", "CS", uom) == Decimal("1") / Decimal("12")
    assert get_contract_price("PROD-EA-005", "2026-03-01", contracts).unit_price == Decimal("2.10")


def test_missing_file_is_empty(tmp_path) -> None:
    assert load_uom_conversions(tmp_path / "nope.json") == {}
    assert load_price_contracts(tmp_path / "nope.json") == {}


def test_missing_conversion_returns_none() -> None:
    uom = load_uom_conversions(REFERENCE / "uom_conversions.json")
    assert get_conversion("PROD-CS-002", "CS", "EA", uom) is None


def test_expired_contract_returns_none() -> None:
    contracts = {"P": [PriceContract("C1", "P", Decimal("1"), "EA", "2025-01-01", "2025-12-31")]}
    assert get_contract_price("P", "2026-03-01", contracts) is None


def test_expired_conversion_returns_none() -> None:
    uom = {"P": [UOMConversion("P", "CS", "EA", Decimal("12"), "2025-01-01", "2025-12-31")]}
    assert get_conversion("P", "CS", "EA", uom, "2026-03-01") is None


def test_overlapping_contracts_raise() -> None:
    contracts = load_price_contracts(REFERENCE / "price_contracts.json")
    with pytest.raises(ReconciliationError, match="overlapping"):
        get_contract_price("PROD-EA-024", "2026-03-01", contracts)


def test_conflicting_conversions_raise() -> None:
    uom = {"P": [UOMConversion("P", "CS", "EA", Decimal("12"), "2026-01-01"),
                 UOMConversion("P", "CS", "EA", Decimal("10"), "2026-01-01")]}
    with pytest.raises(ReconciliationError, match="conflicting"):
        get_conversion("P", "CS", "EA", uom, "2026-03-01")


def test_reference_version_stable() -> None:
    assert reference_version(REFERENCE) == reference_version(REFERENCE)
