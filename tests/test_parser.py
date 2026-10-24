from decimal import Decimal

import pytest

from backend.exceptions import ParseError
from backend.parser import detect_doc_type, parse_invoice, parse_po, parse_receipt
from tests.conftest import fx


def test_valid_po_load_decimal_values() -> None:
    po = parse_po(fx("po_AT01.json"))
    line = po.lines[0]
    assert po.po_number == "PO-AT01"
    assert line.quantity == Decimal("100")
    assert line.unit_price == Decimal("2.00")
    assert isinstance(line.unit_price, Decimal)
    assert line.raw.fields["unit_price"] == "2.00"


def test_missing_quantity_names_field() -> None:
    with pytest.raises(ParseError) as exc:
        parse_invoice(fx("inv_AT11_malformed.json"))
    assert exc.value.field_name == "lines[0].quantity"


def test_price_precision_no_float_drift() -> None:
    inv = parse_invoice(fx("inv_AT13a.json"))
    assert inv.lines[0].unit_price == Decimal("0.335")
    assert str(inv.lines[0].unit_price) == "0.335"


def test_content_hash_stable() -> None:
    assert parse_po(fx("po_AT01.json")).content_hash == parse_po(fx("po_AT01.json")).content_hash
    assert parse_po(fx("po_AT01.json")).content_hash != parse_po(fx("po_AT04.json")).content_hash


def test_currency_captured() -> None:
    assert parse_po(fx("po_AT12.json")).currency == "USD"
    assert parse_invoice(fx("inv_AT12_currency.json")).currency == "EUR"


def test_numeric_json_numbers_rejected() -> None:
    bad = b'{"doc_type": "850", "po_number": "X", "lines": [{"quantity": 1.5}]}'
    with pytest.raises(ParseError, match="must be JSON strings"):
        parse_po(bad)


def test_malformed_envelope() -> None:
    with pytest.raises(ParseError) as exc:
        parse_invoice(b"{not json")
    assert exc.value.field_name == "<envelope>"


def test_unsupported_version() -> None:
    with pytest.raises(ParseError) as exc:
        parse_invoice(fx("inv_AT11_unsupported.json"))
    assert exc.value.field_name == "version"


def test_wrong_doc_type() -> None:
    with pytest.raises(ParseError) as exc:
        parse_po(fx("inv_AT01.json"))
    assert exc.value.field_name == "doc_type"


def test_missing_price_basis_is_none_not_defaulted() -> None:
    assert parse_po(fx("po_AT24.json")).lines[0].price_basis_qty is None


def test_receipt_stored_only() -> None:
    receipt = parse_receipt(fx("receipt_AT21.json"))
    assert receipt.doc_type == "861"
    assert detect_doc_type(fx("receipt_AT21.json")) == "861"
