"""Adapter for the team's EDI JSON datasets (partner profile "team-json-v1").

The team schema (`transaction_type`, nested buyer/supplier, `line_items`, JSON numbers)
is mapped onto the shared models in backend.models. Numbers are read with
parse_float=Decimal / parse_int=Decimal, so every Decimal is built from the literal
source text: no binary float ever exists.

Profile declarations (PRD 18.2 allows f=1 / b=1 only when the profile establishes it):
- unit_price is per one unit of the line's unit_of_measure (price basis b = 1); each
  invoice line is still checked by the rules engine against its line_total.
- Lines are keyed by (line_number, buyer_sku), the rules doc's composite key.

A file may hold one document or an array of documents. Each document's identity is
the SHA-256 of its canonical JSON, so the same document always hashes the same.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.exceptions import ParseError
from backend.models import (
    Invoice,
    InvoiceLine,
    Money,
    POLine,
    PurchaseOrder,
    ReceiptEventType,
    ReceivingAdvice,
    ReceivingAdviceLine,
)

PROFILE = "team-json-v1"
PRICE_BASIS = Decimal("1")  # declared by the profile, see module docstring


def is_team_document(doc: Any) -> bool:
    return isinstance(doc, dict) and "transaction_type" in doc


def loads(data: bytes) -> Any:
    """Parse JSON with exact Decimals for every number."""
    try:
        return json.loads(data, parse_float=Decimal, parse_int=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParseError("<envelope>", None, f"malformed JSON envelope: {exc}") from exc


def load_team_file(source: str | Path | bytes) -> list[dict[str, Any]]:
    """All team-schema documents in a file (single object or array)."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    obj = loads(data)
    docs = obj if isinstance(obj, list) else [obj]
    for idx, doc in enumerate(docs):
        if not is_team_document(doc):
            raise ParseError(f"[{idx}].transaction_type", None, "not a team-schema EDI document")
    return docs


def canonical_bytes(doc: dict[str, Any]) -> bytes:
    """Stable serialization of one document (numbers become exact strings)."""
    return json.dumps(doc, sort_keys=True, default=str, separators=(",", ":")).encode()


def _text(value: Any) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _req(obj: dict[str, Any], *keys: str, where: str = "") -> Any:
    for key in keys:
        if obj.get(key) not in (None, ""):
            return obj[key]
    raise ParseError(f"{where}{keys[0]}", None, "required field is missing")


def _num(obj: dict[str, Any], *keys: str, where: str = "", positive: bool = False,
         default: Decimal | None = None) -> Decimal:
    try:
        value = _req(obj, *keys, where=where)
    except ParseError:
        if default is not None:
            return default
        raise
    name = f"{where}{keys[0]}"
    if isinstance(value, bool) or isinstance(value, float):
        raise ParseError(name, value, "numeric field must be an exact JSON number or string")
    try:
        result = Decimal(_text(value))
    except ArithmeticError as exc:
        raise ParseError(name, value, "not a valid decimal number") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ParseError(name, value, "must be a finite, non-negative number"
                         + (" greater than zero" if positive else ""))
    return result


def _party(doc: dict[str, Any], key: str) -> str:
    value = _req(doc, key)
    if isinstance(value, dict):
        return _text(_req(value, "id", where=f"{key}."))
    return _text(value)


def _date(doc: dict[str, Any], *keys: str) -> date:
    value = _text(_req(doc, *keys))
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(keys[0], value, "date must be YYYY-MM-DD") from exc


def _lines(doc: dict[str, Any]) -> list[dict[str, Any]]:
    lines = doc.get("line_items")
    if not isinstance(lines, list) or not lines:
        raise ParseError("line_items", lines, "at least one line is required")
    return lines


def _currency(doc: dict[str, Any]) -> str:
    if doc.get("currency"):
        return _text(doc["currency"]).upper()
    found = {_text(ln["currency"]).upper() for ln in _lines(doc) if ln.get("currency")}
    if len(found) != 1:
        raise ParseError("currency", sorted(found), "document currency missing or mixed")
    return found.pop()


def _raw(line: dict[str, Any], mapping: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Canonical evidence keys -> exact source strings (used as evidence IDs)."""
    raw: dict[str, str] = {}
    for canon, keys in mapping.items():
        for key in keys:
            if line.get(key) not in (None, ""):
                raw[canon] = _text(line[key])
                break
    return raw


_LINE_KEYS = {
    "line_id": ("line_number",),
    "product_id": ("buyer_sku",),
    "vendor_sku": ("vendor_sku",),
    "upc": ("upc",),
    "uom": ("unit_of_measure",),
    "unit_price": ("unit_price",),
}


def team_po(doc: dict[str, Any], content_hash: str) -> PurchaseOrder:
    currency = _currency(doc)
    lines = []
    for idx, ln in enumerate(_lines(doc)):
        where = f"line_items[{idx}]."
        raw = _raw(ln, {**_LINE_KEYS, "quantity": ("ordered_quantity", "quantity"),
                        "extended_price": ("line_total",)})
        raw["price_basis_qty"] = "1"
        lines.append(POLine(
            line_id=_text(_req(ln, "line_number", where=where)),
            product_id=_text(_req(ln, "buyer_sku", where=where)),
            ordered_qty=_num(ln, "ordered_quantity", "quantity", where=where, positive=True),
            uom=_text(_req(ln, "unit_of_measure", where=where)).upper(),
            unit_price=Money(amount=_num(ln, "unit_price", where=where)),
            price_basis=PRICE_BASIS,
            currency=currency,
            upc=_text(ln.get("upc", "")),
            description=_text(ln.get("description", "")),
            raw=raw,
        ))
    return PurchaseOrder(
        po_id=_text(_req(doc, "purchase_order_number", "po_number")),
        order_date=_date(doc, "purchase_order_date", "po_date"),
        buyer_id=_party(doc, "buyer"),
        supplier_id=_party(doc, "supplier"),
        currency=currency,
        lines=lines,
        content_hash=content_hash,
        raw_fields={"profile": PROFILE, **doc},
    )


def team_invoice(doc: dict[str, Any], content_hash: str) -> Invoice:
    currency = _currency(doc)
    po_id = _text(_req(doc, "purchase_order_number", "po_number"))
    lines = []
    for idx, ln in enumerate(_lines(doc)):
        where = f"line_items[{idx}]."
        raw = _raw(ln, {**_LINE_KEYS, "quantity": ("invoiced_quantity", "quantity"),
                        "extended_price": ("line_total",)})
        raw["price_basis_qty"] = "1"
        total = ln.get("line_total")
        lines.append(InvoiceLine(
            line_id=_text(_req(ln, "line_number", where=where)),
            po_reference=po_id,
            line_reference=_text(_req(ln, "line_number", where=where)),
            product_id=_text(_req(ln, "buyer_sku", where=where)),
            invoiced_qty=_num(ln, "invoiced_quantity", "quantity", where=where, positive=True),
            uom=_text(_req(ln, "unit_of_measure", where=where)).upper(),
            unit_price=Money(amount=_num(ln, "unit_price", where=where)),
            price_basis=PRICE_BASIS,
            currency=currency,
            upc=_text(ln.get("upc", "")),
            description=_text(ln.get("description", "")),
            extended_price=Money(amount=_num(ln, "line_total", where=where))
            if total not in (None, "") else None,
            raw=raw,
        ))
    charges = [
        {"type": key, "amount": _text(doc[key])}
        for key in ("tax", "shipping")
        if doc.get(key) not in (None, "") and Decimal(_text(doc[key])) != 0
    ]
    total = doc.get("invoice_total")
    return Invoice(
        invoice_id=_text(_req(doc, "invoice_number")),
        invoice_date=_date(doc, "invoice_date"),
        po_references=[po_id],
        buyer_id=_party(doc, "buyer"),
        supplier_id=_party(doc, "supplier"),
        currency=currency,
        lines=lines,
        content_hash=content_hash,
        total_amount=Money(amount=_num(doc, "invoice_total")) if total not in (None, "")
        else None,
        charges=charges,
        raw_fields={"profile": PROFILE, **doc},
    )


def _mode(doc: dict[str, Any]) -> ReceiptEventType | None:
    value = _text(doc.get("receipt_quantity_mode", "")).upper()
    return ReceiptEventType(value) if value in ReceiptEventType.__members__ else None


def team_receipt(doc: dict[str, Any], content_hash: str) -> ReceivingAdvice:
    po_id = _text(_req(doc, "purchase_order_number", "po_number"))
    mode = _mode(doc)
    lines = []
    for idx, ln in enumerate(_lines(doc)):
        where = f"line_items[{idx}]."
        received = _num(ln, "received_quantity", where=where)
        lines.append(ReceivingAdviceLine(
            line_id=_text(_req(ln, "line_number", where=where)),
            po_reference=po_id,
            line_reference=_text(_req(ln, "line_number", where=where)),
            product_id=_text(_req(ln, "buyer_sku", where=where)),
            received_qty=received,
            accepted_qty=_num(ln, "accepted_quantity", where=where),
            rejected_qty=_num(ln, "rejected_quantity", where=where, default=Decimal("0")),
            damaged_qty=_num(ln, "damaged_quantity", where=where, default=Decimal("0")),
            uom=_text(_req(ln, "unit_of_measure", where=where)).upper(),
            receipt_event_type=mode,
            condition_review_required=bool(ln.get("condition_review_required", False)),
            rejection_reason=_text(ln["rejection_reason"]) if ln.get("rejection_reason")
            else None,
            receiving_condition=_text(ln["receiving_condition"])
            if ln.get("receiving_condition") else None,
            raw=_raw(ln, {**_LINE_KEYS, "received_quantity": ("received_quantity",),
                          "accepted_quantity": ("accepted_quantity",),
                          "rejected_quantity": ("rejected_quantity",)}),
        ))
    location = doc.get("receiving_location")
    return ReceivingAdvice(
        receipt_id=_text(_req(doc, "receiving_advice_number")),
        receipt_date=_date(doc, "receipt_date", "receiving_date"),
        po_references=[po_id],
        buyer_id=_party(doc, "buyer"),
        supplier_id=_party(doc, "supplier"),
        receipt_event_id=_text(doc["receipt_event_id"]) if doc.get("receipt_event_id")
        else None,
        facility=_text(location.get("id")) if isinstance(location, dict) else None,
        lines=lines,
        content_hash=content_hash,
    )
