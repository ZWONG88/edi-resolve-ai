"""
backend/models/reconciliation.py

Pydantic models for reconciliation policy, per-line results, and case-level results.

All monetary deltas use Money (never float).
All quantity deltas use Decimal (never float).
FindingStatus.NOT_EVALUATED and FindingStatus.CHECK_FAILED are distinct — never aliased.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from backend.models.common import Evidence, FindingStatus, Money


class ReconciliationPolicy(BaseModel):
    """
    Configuration that governs a single reconciliation run.

    The prototype baseline uses:
      currency="USD", decimal_places=2, rounding_mode="ROUND_HALF_UP",
      zero price and quantity tolerances, three_way_enabled=False.

    policy_version is recorded in every audit event so decisions can be
    traced back to the exact rules that produced them.
    """

    currency: str
    decimal_places: int
    rounding_mode: str       # e.g. "ROUND_HALF_UP"
    price_abs_tolerance: Money
    price_rel_tolerance: Decimal
    qty_tolerance: Decimal
    three_way_enabled: bool = False
    policy_version: str

    @field_validator("decimal_places")
    @classmethod
    def _decimal_places_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("decimal_places must be >= 0")
        return v


class LineResult(BaseModel):
    """
    The deterministic outcome of comparing one PO line against one invoice line.

    delta_price / delta_qty are None when the corresponding check was NOT_EVALUATED
    (e.g. due to a missing UOM conversion — the check could not run, it did not fail).

    contract_price_base is displayed as supporting evidence only; it never
    replaces the PO price or overrides the delta calculation.
    """

    line_id: str                              # Unique ID for this result row
    po_line_id: str
    invoice_line_id: str
    status: FindingStatus                     # Per-line outcome from the decision table

    delta_price: Money | None = None          # None when price check was NOT_EVALUATED
    delta_qty: Decimal | None = None          # None when qty check was NOT_EVALUATED
    normalized_po_qty: Decimal | None = None  # None if UOM conversion was missing
    normalized_invoice_qty: Decimal | None = None
    po_unit_price_base: Money | None = None   # PO price normalized to base UOM
    invoice_unit_price_base: Money | None = None
    contract_price_base: Money | None = None  # Contract price (evidence only; never overwrites PO)

    allocated_qty: Decimal                    # Qty allocated from this invoice line against the PO
    evidence: list[Evidence] = []             # Field-level pointers supporting this result
    checks_not_evaluated: list[str] = []      # Names of skipped checks, e.g. ["price_check"]
    ai_explanation: str | None = None         # Populated by orchestrator after AI call


class CaseResult(BaseModel):
    """
    The complete outcome of one reconciliation run for a PO + invoice pair.

    case_id is stable across runs for the same PO+invoice pair.
    run_id is unique per reconciliation run.
    case_version is a monotonically increasing integer starting at 1;
      it is used for optimistic locking in submit_review().
    """

    case_id: str         # UUID; stable across re-runs for the same PO+invoice
    run_id: str          # UUID; unique per reconciliation execution
    case_version: int    # Monotonically increasing; starts at 1

    po_id: str
    invoice_id: str
    overall_status: FindingStatus   # Worst-case status across all line_results
    line_results: list[LineResult]
    policy_version: str
    run_timestamp: datetime         # Must be UTC

    # Populated after AI call; None if not yet attempted
    ai_result_status: str | None = None  # "OK" | "AI_ABSTAIN" | "AI_UNAVAILABLE"

    @field_validator("case_version")
    @classmethod
    def _version_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("case_version must be >= 1")
        return v
