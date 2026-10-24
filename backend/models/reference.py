"""
backend/models/reference.py

Pydantic models for reference data:
  - UOMConversion  — verified unit-of-measure conversion factors
  - PriceContract  — agreed prices with effective date ranges
  - ProductMaster  — canonical product identifiers and associated conversions
  - CatalogProduct — synthetic master-catalogue entry (display enrichment only)

These are loaded from data/reference/ JSON files by reference_store.py.
Missing, expired, or contradictory reference records yield INSUFFICIENT_EVIDENCE.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_validator

from backend.models.common import Money


class UOMConversion(BaseModel):
    """
    A verified conversion factor between two units of measure.

    factor: number of to_uom units per one from_uom unit.
    Example: from_uom="CS", to_uom="EA", factor=Decimal("12") means 1 case = 12 eaches.

    Scope: if product_id is set, applies only to that product.
           If None, the conversion is universal.
    """

    from_uom: str
    to_uom: str
    factor: Decimal          # Must be > 0
    product_id: str | None = None  # None = universal conversion
    effective_date: date
    expiry_date: date | None = None  # None = no expiry
    source: str              # e.g. "product_master_v1"
    approver: str | None = None

    @field_validator("factor")
    @classmethod
    def _factor_positive(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("UOMConversion factor must be > 0")
        return v


class PriceContract(BaseModel):
    """
    A contractually agreed price for a product, scoped to an optional trading partner
    and valid within an effective date range.

    Contract prices are displayed as supporting evidence alongside PO and invoice prices.
    They never overwrite the PO price or establish an authorized amendment.
    """

    contract_id: str
    product_id: str
    agreed_price: Money
    price_basis: int         # Must be > 0
    currency: str
    effective_from: date
    effective_to: date | None = None
    partner_scope: str | None = None  # If set, applies only to this buyer/supplier pair
    policy_version: str

    @field_validator("price_basis")
    @classmethod
    def _basis_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("price_basis must be > 0")
        return v


class ProductMaster(BaseModel):
    """
    Canonical product record including cross-reference identifiers and UOM conversions.

    An empty uom_conversions list does not raise an error here — the comparator
    treats it as INSUFFICIENT_EVIDENCE for any UOM check that requires a conversion.
    """

    product_id: str
    description: str
    uom_conversions: list[UOMConversion] = []  # May be empty
    identifiers: dict[str, str] = {}           # e.g. {"UPC": "012345678901", "buyer_sku": "X123"}


# Selection methods in the synthetic master catalogue that did not come from a single,
# unanimous source value (random picks, ranges, pack inferences).
SYNTHETIC_METHODS = ("random_within_range", "random_observed", "synthetic_inference")


class CatalogSelection(BaseModel):
    """How the catalogue builder chose a field value from its observed candidates."""

    value: str | int | None = None
    candidates: list[str | int] = []
    conflict: bool = False
    method: str
    minimum: str | None = None
    maximum: str | None = None

    @property
    def is_synthetic(self) -> bool:
        return self.conflict or self.method.startswith(SYNTHETIC_METHODS)


class CatalogPriceRecord(BaseModel):
    """A catalogue price per currency/UOM. Display only — never an agreed price."""

    currency: str
    unit_of_measure: str
    price_basis_quantity: int | None = None
    unit_price: Decimal          # Full precision; never rounded to cents
    selection: CatalogSelection


class CatalogProduct(BaseModel):
    """
    One product from data/reference/master_catalog.json (synthetic, generated).

    Enrichment only (PRD 17.3): a catalogue cannot establish pack size, agreed price,
    receipt truth or identity for financial decisions, so the comparator never sees it.
    """

    catalog_product_id: str
    concatenated_key: str
    buyer_id: str
    supplier_id: str
    upc: str | None = None
    buyer_skus: list[str] = []
    vendor_skus: list[str] = []
    product_name: str | None = None
    master_uom: str | None = None
    pack_size: int | None = None
    pack_size_origin: str | None = None
    price_records: list[CatalogPriceRecord] = []
    field_selections: dict[str, CatalogSelection] = {}
    review_flags: list[str] = []
