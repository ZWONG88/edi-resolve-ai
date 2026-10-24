"""
backend/models/documents.py

Pydantic models for EDI transaction documents:
  - PurchaseOrder (EDI 850) and its line items
  - Invoice (EDI 810) and its line items
  - ReceivingAdvice (EDI 861) and its line items — P2 schema defined now for stability

All monetary fields use Money (never float).
All quantity fields use Decimal (never float).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_validator

from backend.models.common import Money, ReceiptEventType


# ---------------------------------------------------------------------------
# EDI 850 — Purchase Order
# ---------------------------------------------------------------------------


class POLine(BaseModel):
    """A single line item on a Purchase Order."""

    line_id: str       # PO-unique line identifier
    product_id: str    # Qualified product identifier (SKU, UPC, buyer item number, etc.)
    ordered_qty: Decimal
    uom: str           # Unit of measure code, e.g. "EA", "CS"
    unit_price: Money  # Price per price_basis units of uom
    price_basis: int = 1  # Number of UOM units the price covers; must be > 0
    currency: str      # ISO 4217, e.g. "USD"

    @field_validator("ordered_qty")
    @classmethod
    def _qty_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("ordered_qty must be > 0")
        return v

    @field_validator("price_basis")
    @classmethod
    def _basis_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("price_basis must be > 0")
        return v


class PurchaseOrder(BaseModel):
    """EDI 850 Purchase Order document."""

    po_id: str                 # Trading-partner-unique PO number
    order_date: date           # Used for reference data effective-date lookups
    buyer_id: str
    supplier_id: str
    currency: str              # Document-level currency (per-line currency may differ)
    lines: list[POLine]        # At least one line required
    content_hash: str          # "sha256:<hex>" of original source bytes; used for dedup
    source_path: str | None = None  # Path to raw source file, for provenance

    @field_validator("lines")
    @classmethod
    def _at_least_one_line(cls, v: list[POLine]) -> list[POLine]:
        if not v:
            raise ValueError("PurchaseOrder must have at least one line")
        return v


# ---------------------------------------------------------------------------
# EDI 810 — Invoice
# ---------------------------------------------------------------------------


class InvoiceLine(BaseModel):
    """A single line item on an Invoice."""

    line_id: str          # Invoice-unique line identifier
    po_reference: str     # PO ID this line references
    line_reference: str   # PO line ID this line references
    product_id: str       # Should match the referenced PO line's product_id
    invoiced_qty: Decimal
    uom: str
    unit_price: Money     # Invoice unit price
    price_basis: int = 1  # Must be > 0
    currency: str         # ISO 4217

    @field_validator("invoiced_qty")
    @classmethod
    def _qty_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("invoiced_qty must be > 0")
        return v

    @field_validator("price_basis")
    @classmethod
    def _basis_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("price_basis must be > 0")
        return v


class Invoice(BaseModel):
    """EDI 810 Invoice document."""

    invoice_id: str
    invoice_date: date
    po_references: list[str]   # All PO IDs referenced; at least one required
    currency: str
    lines: list[InvoiceLine]
    content_hash: str
    source_path: str | None = None

    @field_validator("po_references")
    @classmethod
    def _at_least_one_po_ref(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Invoice must reference at least one PO")
        return v

    @field_validator("lines")
    @classmethod
    def _at_least_one_line(cls, v: list[InvoiceLine]) -> list[InvoiceLine]:
        if not v:
            raise ValueError("Invoice must have at least one line")
        return v


# ---------------------------------------------------------------------------
# EDI 861 — Receiving Advice  (P2 schema; defined now for schema stability)
# ---------------------------------------------------------------------------


class ReceivingAdviceLine(BaseModel):
    """
    A single line on a Receiving Advice.
    P2 feature — schema is defined now so it is stable; reconciliation logic
    is gated behind three_way_enabled=True.
    """

    line_id: str
    po_reference: str       # PO ID
    line_reference: str     # PO line ID
    product_id: str
    received_qty: Decimal   # Physical count received; >= 0
    accepted_qty: Decimal   # Quantity accepted for billing; >= 0; <= received_qty
    rejected_qty: Decimal   # >= 0
    damaged_qty: Decimal    # >= 0; may overlap with rejected_qty (partner-profile-dependent)
    uom: str
    receipt_event_type: ReceiptEventType  # INCREMENTAL or CUMULATIVE

    @field_validator("received_qty", "accepted_qty", "rejected_qty", "damaged_qty")
    @classmethod
    def _qty_non_negative(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("Receipt quantities must be >= 0")
        return v


class ReceivingAdvice(BaseModel):
    """EDI 861 Receiving Advice document."""

    receipt_id: str
    receipt_date: date
    po_references: list[str]
    facility: str | None = None   # Receiving facility identifier
    lines: list[ReceivingAdviceLine]
    content_hash: str
    source_path: str | None = None

    @field_validator("po_references")
    @classmethod
    def _at_least_one_po_ref(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("ReceivingAdvice must reference at least one PO")
        return v

    @field_validator("lines")
    @classmethod
    def _at_least_one_line(cls, v: list[ReceivingAdviceLine]) -> list[ReceivingAdviceLine]:
        if not v:
            raise ValueError("ReceivingAdvice must have at least one line")
        return v
