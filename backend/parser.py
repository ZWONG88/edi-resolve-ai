"""Load and validate canonical EDI JSON documents (850 PO, 810 invoice, 861 receipt).

Output is the shared Pydantic contract in backend.models. Every parsed object carries
"sha256:<hex>" of the exact source bytes and keeps the original strings next to the
Decimal / Money values. Nothing is ever defaulted: a missing price basis stays None.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend import adapters
from backend.exceptions import ParseError
from backend.models import (
    Invoice,
    InvoiceLine,
    Money,
    POLine,
    PurchaseOrder,
    ReceivingAdvice,
    StoredDocument,
)

PARSER_VERSION = "1.1.0"
SUPPORTED_VERSIONS = {"1.0"}
SUPPORTED_DOC_TYPES = {"850", "810", "861"}

_PO_HEADER = ["po_number", "po_date", "buyer", "supplier", "currency"]
_INV_HEADER = ["invoice_number", "invoice_date", "po_number", "buyer", "supplier", "currency"]
_LINE_REQUIRED = ["line_id", "product_id", "uom"]

Source = str | Path | bytes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reject_number(value: str) -> Any:
    raise ParseError("<numeric>", value, "numeric values must be JSON strings, not numbers")


def _load(source: Source) -> tuple[bytes, dict[str, Any], str | None]:
    path = None if isinstance(source, bytes) else str(source)
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        doc = json.loads(data, parse_float=_reject_number, parse_int=_reject_number)
    except ParseError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ParseError("<envelope>", None, f"malformed JSON envelope: {exc}") from exc
    if not isinstance(doc, dict):
        raise ParseError("<envelope>", None, "document root must be a JSON object")
    return data, doc, path


def content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _team_doc(data: bytes, expected: str) -> dict[str, Any] | None:
    """The document if `data` is one team-schema document (see backend/adapters.py)."""
    try:
        obj = adapters.loads(data)
    except ParseError:
        return None  # the strict path reports the envelope error
    if isinstance(obj, list):
        if obj and all(adapters.is_team_document(d) for d in obj):
            raise ParseError("<envelope>", f"{len(obj)} documents",
                             "file holds several documents; use dataset import")
        return None
    if not adapters.is_team_document(obj):
        return None
    found = str(obj["transaction_type"])
    if found != expected:
        raise ParseError("transaction_type", found, f"expected document type {expected}")
    return obj


def _adapt(builder: Any, doc: dict[str, Any], path: str | None) -> Any:
    canonical = adapters.canonical_bytes(doc)
    try:
        model = builder(doc, content_hash(canonical))
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        raise ParseError(loc, err.get("input"), err["msg"]) from exc
    return model.model_copy(update={"source_path": path, "ingested_at": _now()}) if hasattr(
        model, "ingested_at") else model.model_copy(update={"source_path": path})


def normalize_source(data: bytes) -> bytes:
    """Bytes stored for a document: canonical JSON for team-schema documents."""
    try:
        obj = adapters.loads(data)
    except ParseError:
        return data
    return adapters.canonical_bytes(obj) if adapters.is_team_document(obj) else data


def _require_str(obj: dict[str, Any], key: str, prefix: str = "") -> str:
    name = f"{prefix}{key}"
    if key not in obj:
        raise ParseError(name, None, "required field is missing")
    value = obj[key]
    if not isinstance(value, str) or not value.strip():
        raise ParseError(name, value, "required field must be a non-empty string")
    return value.strip()


def _date(obj: dict[str, Any], key: str) -> date:
    value = _require_str(obj, key)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(key, value, "date must be YYYY-MM-DD") from exc


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


def _required_decimal(obj: dict[str, Any], key: str, prefix: str, positive: bool) -> Decimal:
    if key not in obj:
        raise ParseError(prefix + key, None, "required field is missing")
    return _decimal(obj[key], prefix + key, positive=positive)


def _optional_decimal(obj: dict[str, Any], key: str, name: str) -> Decimal | None:
    value = obj.get(key)
    if value is None or value == "":
        return None
    return _decimal(value, name, positive=(key == "price_basis_qty"))


def _money(value: Decimal | None) -> Money | None:
    # Full precision: never Money.from_str, which rounds (PRD 18.1).
    return None if value is None else Money(amount=value)


def _check_envelope(doc: dict[str, Any], expected: str) -> None:
    doc_type = doc.get("doc_type")
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise ParseError("doc_type", doc_type, "unsupported or missing document type")
    if doc_type != expected:
        raise ParseError("doc_type", doc_type, f"expected document type {expected}")
    version = doc.get("version", "1.0")
    if version not in SUPPORTED_VERSIONS:
        raise ParseError("version", version, "unsupported document version")


def _raw_lines(doc: dict[str, Any]) -> list[dict[str, Any]]:
    lines = doc.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ParseError("lines", lines, "at least one line is required")
    for idx, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ParseError(f"lines[{idx}]", line, "line must be an object")
        for key in _LINE_REQUIRED:
            _require_str(line, key, f"lines[{idx}].")
    return lines


def _line_common(line: dict[str, Any], idx: int, currency: str) -> dict[str, Any]:
    prefix = f"lines[{idx}]."
    return {
        "line_id": line["line_id"].strip(),
        "product_id": line["product_id"].strip(),
        "uom": line["uom"].strip().upper(),
        "unit_price": _money(_required_decimal(line, "unit_price", prefix, positive=False)),
        "price_basis": _optional_decimal(line, "price_basis_qty", prefix + "price_basis_qty"),
        "currency": currency,
        "upc": str(line.get("upc", "")).strip(),
        "description": str(line.get("description", "")),
        "raw": {k: v for k, v in line.items() if isinstance(v, str)},
    }


def _build(model: type, field_prefix: str, **kwargs: Any) -> Any:
    """Construct a Pydantic model; validation errors name the exact field."""
    try:
        return model(**kwargs)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"])
        raise ParseError(f"{field_prefix}{loc}", err.get("input"), err["msg"]) from exc


def _read(source: Source) -> tuple[bytes, str | None]:
    if isinstance(source, bytes):
        return source, None
    return Path(source).read_bytes(), str(source)


def parse_po(source: Source) -> PurchaseOrder:
    raw, path = _read(source)
    if (team := _team_doc(raw, "850")) is not None:
        return _adapt(adapters.team_po, team, path)
    data, doc, _ = _load(raw)
    _check_envelope(doc, "850")
    header = {key: _require_str(doc, key) for key in _PO_HEADER}
    currency = header["currency"].upper()
    lines = []
    for idx, line in enumerate(_raw_lines(doc)):
        qty = _required_decimal(line, "quantity", f"lines[{idx}].", positive=True)
        common = _line_common(line, idx, currency)
        lines.append(_build(POLine, f"lines[{idx}].", ordered_qty=qty, **common))
    return _build(
        PurchaseOrder, "",
        po_id=header["po_number"],
        order_date=_date(doc, "po_date"),
        buyer_id=header["buyer"],
        supplier_id=header["supplier"],
        currency=currency,
        lines=lines,
        content_hash=content_hash(data),
        source_path=path,
        ingested_at=_now(),
        raw_fields=doc,
    )


def parse_invoice(source: Source) -> Invoice:
    raw, path = _read(source)
    if (team := _team_doc(raw, "810")) is not None:
        return _adapt(adapters.team_invoice, team, path)
    data, doc, _ = _load(raw)
    _check_envelope(doc, "810")
    header = {key: _require_str(doc, key) for key in _INV_HEADER}
    currency = header["currency"].upper()
    charges = doc.get("charges") or []
    if not isinstance(charges, list):
        raise ParseError("charges", charges, "charges must be a list")
    lines = []
    for idx, line in enumerate(_raw_lines(doc)):
        prefix = f"lines[{idx}]."
        qty = _required_decimal(line, "quantity", prefix, positive=True)
        common = _line_common(line, idx, currency)
        ext = _optional_decimal(line, "extended_price", prefix + "extended_price")
        ref = str(line.get("po_line_ref", "")).strip() or None
        lines.append(_build(
            InvoiceLine, prefix, invoiced_qty=qty, po_reference=header["po_number"],
            line_reference=ref, extended_price=_money(ext), **common,
        ))
    return _build(
        Invoice, "",
        invoice_id=header["invoice_number"],
        invoice_date=_date(doc, "invoice_date"),
        po_references=[header["po_number"]],
        buyer_id=header["buyer"],
        supplier_id=header["supplier"],
        currency=currency,
        lines=lines,
        content_hash=content_hash(data),
        source_path=path,
        total_amount=_money(_optional_decimal(doc, "total_amount", "total_amount")),
        charges=[c for c in charges if isinstance(c, dict)],
        ingested_at=_now(),
        raw_fields=doc,
    )


def _receipt_model(doc: dict[str, Any]) -> ReceivingAdvice | None:
    """Validate against the P2 ReceivingAdvice schema when possible; store-only otherwise."""
    try:
        return ReceivingAdvice(
            receipt_id=doc["receipt_number"],
            receipt_date=date.fromisoformat(doc["receipt_date"]),
            po_references=[doc["po_number"]],
            buyer_id=doc.get("buyer"),
            supplier_id=doc.get("supplier"),
            receipt_event_id=doc.get("receipt_event_id"),
            facility=doc.get("facility"),
            content_hash="",
            lines=[
                {
                    "line_id": ln["line_id"],
                    "po_reference": doc["po_number"],
                    "line_reference": ln.get("po_line_ref", ln["line_id"]),
                    "product_id": ln["product_id"],
                    "received_qty": Decimal(ln["received_quantity"]),
                    "accepted_qty": Decimal(ln["accepted_quantity"]),
                    "rejected_qty": Decimal(ln.get("rejected_quantity", "0")),
                    "damaged_qty": Decimal(ln.get("damaged_quantity", "0")),
                    "uom": ln["uom"],
                    "receipt_event_type": ln.get("event_type"),
                }
                for ln in doc["lines"]
            ],
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, ValidationError):
        return None


def parse_receipt(source: Source) -> StoredDocument:
    """Parse an 861 into a StoredDocument; `receipt` is set when it fits the schema."""
    raw, path = _read(source)
    if (team := _team_doc(raw, "861")) is not None:
        receipt = _adapt(adapters.team_receipt, team, path)
        return StoredDocument(doc_type="861", content_hash=receipt.content_hash,
                              ingested_at=_now(), raw_fields=team, receipt=receipt)
    data, doc, _ = _load(raw)
    _check_envelope(doc, "861")
    receipt = _receipt_model(doc)
    if receipt is not None:
        receipt = receipt.model_copy(update={"content_hash": content_hash(data)})
    return StoredDocument(doc_type="861", content_hash=content_hash(data), ingested_at=_now(),
                          raw_fields=doc, receipt=receipt)


def detect_doc_type(source: Source) -> str:
    raw, _ = _read(source)
    obj = adapters.loads(raw)
    if adapters.is_team_document(obj):
        return str(obj["transaction_type"])
    _, doc, _ = _load(raw)
    doc_type = doc.get("doc_type")
    if doc_type not in SUPPORTED_DOC_TYPES:
        raise ParseError("doc_type", doc_type, "unsupported or missing document type")
    return doc_type
