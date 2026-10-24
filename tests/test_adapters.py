"""Team dataset adapter (profile team-json-v1)."""

from decimal import Decimal

import pytest

from backend import adapters, parser
from backend.exceptions import ParseError
from tests import team_docs
from tests.conftest import DATASETS


def test_team_po_mapping() -> None:
    po = parser.parse_po(DATASETS / "dataset1" / "edi_850_50_line_items.json")
    line = po.lines[0]
    assert (po.po_id, po.buyer_id, po.supplier_id, po.currency) == (
        "PO-10001", "BUYER001", "SUP001", "USD")
    assert (line.line_id, line.product_id, line.uom) == ("1", "SKU0001", "EA")
    assert line.ordered_qty == Decimal("17")
    assert line.price_basis == Decimal("1")  # declared by the profile
    assert line.raw["unit_price"] == "4.62"


def test_numbers_are_exact_decimals() -> None:
    docs = adapters.load_team_file(DATASETS / "dataset2" / "edi_810_dataset.json")
    inv = adapters.team_invoice(docs[0], "h")
    assert inv.lines[1].unit_price.amount == Decimal("1.65")
    assert str(inv.lines[1].unit_price.amount) == "1.65"  # no binary float drift


def test_array_files_split_into_documents() -> None:
    docs = adapters.load_team_file(DATASETS / "dataset2" / "edi_850_dataset.json")
    assert len(docs) == 60
    with pytest.raises(ParseError, match="several documents"):
        parser.parse_po(DATASETS / "dataset2" / "edi_850_dataset.json")


def test_canonical_hash_is_stable() -> None:
    raw = (DATASETS / "dataset1" / "edi_810_PO-10001.json").read_bytes()
    once = parser.normalize_source(raw)
    assert parser.normalize_source(once) == once
    assert parser.parse_invoice(raw).content_hash == parser.parse_invoice(once).content_hash


def test_team_receipt_mapping() -> None:
    stored = parser.parse_receipt(
        team_docs.receipt([(1, "A", "10", "8", "2", "EA")], mode="incremental"))
    line = stored.receipt.lines[0]
    assert (line.received_qty, line.accepted_qty, line.rejected_qty) == (
        Decimal("10"), Decimal("8"), Decimal("2"))
    assert line.receipt_event_type == "INCREMENTAL"
    assert stored.receipt.buyer_id == team_docs.BUYER


def test_missing_required_field_named() -> None:
    bad = team_docs.invoice([(1, "A", "1", "EA", "2.00", "2.00")]).replace(
        b'"invoiced_quantity": "1", ', b"")
    with pytest.raises(ParseError) as exc:
        parser.parse_invoice(bad)
    assert exc.value.field_name == "line_items[0].invoiced_quantity"


def test_wrong_transaction_type() -> None:
    with pytest.raises(ParseError, match="expected document type 850"):
        parser.parse_po(team_docs.invoice([(1, "A", "1", "EA", "2.00", "2.00")]))
