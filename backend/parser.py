"""Load and validate canonical EDI JSON documents (850 PO, 810 invoice, 861 receipt).

Every parsed object carries the SHA-256 of the exact source bytes and keeps the
original string values next to the Decimal casts. Nothing is ever defaulted.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from backend.exceptions import ParseError
from backend.models import Invoice, LineItem, PurchaseOrder, RawLineItem, StoredDocument

PARSER_VERSION = "1.0.0"
SUPPORTED_VERSIONS = {"1.0"}
SUPPORTED_DOC_TYPES = {"850", "810", "861"}

_PO_HEADER = ["po_number", "po_date", "buyer", "supplier", "currency"]
_INV_HEADER = ["invoice_number", "invoice_date", "po_number", "buyer", "supplier", "currency"]
_LINE_REQUIRED = ["line_id", "product_id", "quantity", "uom", "unit_price"]

Source = str | Path | bytes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_float(value: str) -> Any:
    raise ParseError("<numeric>", value, "numeric values must be JSON strings, not numbers")


def _load(source: Source) -> tuple[bytes, dict[str, Any]]:
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        doc = json.loads(data, parse_float=_reject_float, parse_int=_reject_float)
    except ParseError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParseError("<envelope>", None, f"malformed JSON envelope: {exc}") from exc
    if not isinstance(doc, dict):
        raise ParseError("<envelope>", None, "document root must be a JSON object")
    return data, doc


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_str(obj: dict[str, Any], key: str, prefix: str = "") -> str:
    name = f"{prefix}{key}"
    if key not in obj:
        raise ParseError(name, None, "required field is missing")
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        raise ParseError(name, value, "required field must be a non-empty string")
    return value.strip()


def _decimal(value: Any, name: str, positive: bool = False) -> Decimal:
    if not isinstance(value, str):
        raise ParseError(name, value, "numeric field must be a string")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ParseError(name, value, "not a valid decimal number") from exc
    if not result.is_finite():
        raise ParseError(name, value, "not a finite decimal number")
    if positive and result <= 0:
        raise ParseError(name, value, "must be greater than zero")
    if result < 0:
        raise ParseError(name, value, "must not be negative")
    return result


def _optional_decimal(obj: dict[str, Any], key: str, name: str) -> Decimal | None:
    value = obj.get(key)
    if value is None or value == "":
        return None
    return _decimal(value, name, positive=(key == "price_basis_qty"))


def _check_envelope(doc: dict[str, Any], expected: str) -> None:
    doc_type = doc.get("doc_type")
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise ParseError("doc_type", doc_type, "unsupported or missing document type")
    if doc_type != expected:
        raise ParseError("doc_type", doc_type, f"expected document type {expected}")
    version = doc.get("version", "1.0")
    if version not in SUPPORTED_VERSIONS:
        raise ParseError("version", version, "unsupported document version")


def _parse_lines(doc: dict[str, Any], with_extension: bool) -> list[LineItem]:
    lines = doc.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ParseError("lines", lines, "at least one line is required")
    parsed: list[LineItem] = []
    for idx, line in enumerate(lines):
        prefix = f"lines[{idx}]."
        if not isinstance(line, dict):
            raise ParseError(f"lines[{idx}]", line, "line must be an object")
        for key in _LINE_REQUIRED:
            if key in ("quantity", "unit_price"):
                if key not in line:
                    raise ParseError(prefix + key, None, "required field is missing")
            else:
                _require_str(line, key, prefix)
        raw = {k: v for k, v in line.items() if isinstance(v, str)}
        parsed.append(
            LineItem(
                line_id=line["line_id"].strip(),
                product_id=line["product_id"].strip(),
                upc=str(line.get("upc", "")).strip(),
                description=str(line.get("description", "")),
                quantity=_decimal(line["quantity"], prefix + "quantity", positive=True),
                uom=line["uom"].strip().upper(),
                unit_price=_decimal(line["unit_price"], prefix + "unit_price"),
                price_basis_qty=_optional_decimal(
                    line, "price_basis_qty", prefix + "price_basis_qty"
                ),
                extended_price=(
                    _optional_decimal(line, "extended_price", prefix + "extended_price")
                    if with_extension
                    else None
                ),
                raw=RawLineItem(fields=raw),
            )
        )
    return parsed


def parse_po(source: Source) -> PurchaseOrder:
    data, doc = _load(source)
    _check_envelope(doc, "850")
    header = {key: _require_str(doc, key) for key in _PO_HEADER}
    return PurchaseOrder(
        po_number=header["po_number"],
        po_date=header["po_date"],
        buyer=header["buyer"],
        supplier=header["supplier"],
        currency=header["currency"].upper(),
        lines=_parse_lines(doc, with_extension=False),
        content_hash=content_hash(data),
        ingested_at=_now(),
        raw_fields=doc,
    )


def parse_invoice(source: Source) -> Invoice:
    data, doc = _load(source)
    _check_envelope(doc, "810")
    header = {key: _require_str(doc, key) for key in _INV_HEADER}
    charges = doc.get("charges") or []
    if not isinstance(charges, list):
        raise ParseError("charges", charges, "charges must be a list")
    return Invoice(
        invoice_number=header["invoice_number"],
        invoice_date=header["invoice_date"],
        po_number=header["po_number"],
        buyer=header["buyer"],
        supplier=header["supplier"],
        currency=header["currency"].upper(),
        lines=_parse_lines(doc, with_extension=True),
        content_hash=content_hash(data),
        ingested_at=_now(),
        raw_fields=doc,
        total_amount=_optional_decimal(doc, "total_amount", "total_amount"),
        charges=[c for c in charges if isinstance(c, dict)],
    )


def parse_receipt(source: Source) -> StoredDocument:
    """Store an 861 without semantic parsing (P2 is disabled in P0)."""
    data, doc = _load(source)
    _check_envelope(doc, "861")
    return StoredDocument("861", content_hash(data), _now(), doc)


def detect_doc_type(source: Source) -> str:
    _, doc = _load(source)
    doc_type = doc.get("doc_type")
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise ParseError("doc_type", doc_type, "unsupported or missing document type")
    return doc_type
