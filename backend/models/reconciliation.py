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
from enum import StrEnum

from pydantic import BaseModel, field_validator

from backend.models.common import CheckStatus, Evidence, FindingStatus, Money

# Case-state precedence, most severe first. A case takes the first state present
# among its findings; with none it is MATCHED_2WAY. All findings are still kept.
STATE_PRECEDENCE: list[FindingStatus] = [
    FindingStatus.QUARANTINED,
    FindingStatus.DUPLICATE_CONFLICT,
    FindingStatus.TECHNICAL_HOLD,
    FindingStatus.WAIT_DOCUMENT,
    FindingStatus.WAIT_RECEIPT,
    FindingStatus.REVIEW_LINK,
    FindingStatus.INSUFFICIENT_EVIDENCE,
    FindingStatus.NOT_EVALUATED,
    FindingStatus.RECEIPT_REVIEW,
    FindingStatus.OVER_RECEIPT,
    FindingStatus.UNMAPPED_LINE_ITEM,
    FindingStatus.QUANTITY_VARIANCE,
    FindingStatus.RECEIPT_VARIANCE,
    FindingStatus.PRICE_VARIANCE,
    FindingStatus.PARTIAL_BILLING,
    FindingStatus.MATCHED_3WAY,
    FindingStatus.MATCHED_2WAY,
]

# States that block a verified pass or a closing approval.
BLOCKING_STATES: set[FindingStatus] = {
    FindingStatus.QUARANTINED,
    FindingStatus.DUPLICATE_CONFLICT,
    FindingStatus.TECHNICAL_HOLD,
    FindingStatus.WAIT_DOCUMENT,
    FindingStatus.REVIEW_LINK,
    FindingStatus.INSUFFICIENT_EVIDENCE,
    FindingStatus.NOT_EVALUATED,
    FindingStatus.WAIT_RECEIPT,
    FindingStatus.RECEIPT_REVIEW,
    FindingStatus.OVER_RECEIPT,
}


class Disposition(StrEnum):
    """Invoice-level outcome from the team's 3-way rules (Stage 5), plus PRD holds."""

    AUTO_APPROVED = "AUTO_APPROVED"                  # 3-way clean; blocked cash == 0
    APPROVED_WITH_SHORT_PAY = "APPROVED_WITH_SHORT_PAY"  # 0 < blocked <= high-value threshold
    NEEDS_HUMAN_AP_REVIEW = "NEEDS_HUMAN_AP_REVIEW"  # blocked > high-value threshold
    HOLD = "HOLD"                                    # blocking evidence problem (PRD)
    PO_HEADER_MISMATCH = "PO_HEADER_MISMATCH"        # no matching PO for this partner
    DUPLICATE_INVOICE_REJECTED = "DUPLICATE_INVOICE_REJECTED"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    RECEIPT_NOT_VERIFIED = "RECEIPT_NOT_VERIFIED"    # 2-way mode: no auto-approval or 820


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
    de_minimis_line_tolerance: Money = Money(amount=Decimal("1.00"))   # rules doc 4.1
    high_value_hold_threshold: Money = Money(amount=Decimal("500.00"))  # rules doc 4.2

    @classmethod
    def baseline(cls) -> "ReconciliationPolicy":
        """Prototype baseline (PRD 18.1): USD, 2 dp, ROUND_HALF_UP, zero tolerances.

        Check tolerances stay zero (every variance is recorded). The dollar thresholds
        only affect the invoice disposition (rules doc 4.1 / 4.2).
        """
        return cls(
            currency="USD",
            decimal_places=2,
            rounding_mode="ROUND_HALF_UP",
            price_abs_tolerance=Money(amount=Decimal("0")),
            price_rel_tolerance=Decimal("0"),
            qty_tolerance=Decimal("0"),
            policy_version="P0-baseline-v1",
        )

    @field_validator("decimal_places")
    @classmethod
    def _decimal_places_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("decimal_places must be >= 0")
        return v


class Finding(BaseModel):
    """Result of one deterministic check on one line (or on the invoice header)."""

    finding_id: str
    check: str                          # e.g. "unit_price", "quantity", "uom_conversion"
    rule_id: str | None = None          # Team rules doc ID, e.g. "R_PRICE_VARIANCE"
    status: CheckStatus
    result_state: FindingStatus | None  # State this finding drives; None = informational
    message: str
    po_value: str | None = None
    invoice_value: str | None = None
    normalized_po_value: str | None = None
    normalized_invoice_value: str | None = None
    delta: str | None = None
    tolerance_applied: str | None = None
    evidence_ids: list[str] = []


class LineResult(BaseModel):
    """
    The deterministic outcome of comparing one PO line against one invoice line.

    delta_price / delta_qty are None when the corresponding check was NOT_EVALUATED
    (e.g. due to a missing UOM conversion — the check could not run, it did not fail).

    contract_price_base is displayed as supporting evidence only; it never
    replaces the PO price or overrides the delta calculation.
    """

    line_id: str                              # Unique ID for this result row
    po_line_id: str | None = None             # None when the line could not be linked
    invoice_line_id: str
    status: FindingStatus                     # Per-line outcome from the decision table

    delta_price: Money | None = None          # None when price check was NOT_EVALUATED
    delta_qty: Decimal | None = None          # None when qty check was NOT_EVALUATED
    normalized_po_qty: Decimal | None = None  # None if UOM conversion was missing
    normalized_invoice_qty: Decimal | None = None
    po_unit_price_base: Money | None = None   # PO price normalized to base UOM
    invoice_unit_price_base: Money | None = None
    contract_price_base: Money | None = None  # Contract price (evidence only; never overwrites PO)

    allocated_qty: Decimal = Decimal("0")     # Qty allocated from this invoice line against the PO
    evidence: list[Evidence] = []             # Field-level pointers supporting this result
    checks_not_evaluated: list[str] = []      # Names of skipped checks, e.g. ["price_check"]
    ai_explanation: str | None = None         # Populated by orchestrator after AI call

    # Side-by-side display values (strings, exactly as computed) and per-check findings
    po_value_raw: dict[str, str] = {}
    invoice_value_raw: dict[str, str] = {}
    po_value_normalized: dict[str, str] = {}
    invoice_value_normalized: dict[str, str] = {}
    delta: dict[str, str] = {}
    tolerance_applied: str = ""
    findings: list[Finding] = []

    # Dollar impact (rules doc Stage 3-4); None when the line could not be evaluated
    accepted_qty: Decimal | None = None        # Accepted receipt available, base UOM (3-way)
    payable_qty: Decimal | None = None
    invoiced_amount: Money | None = None
    approved_amount: Money | None = None
    blocked_amount: Money | None = None
    waived_amount: Money | None = None         # De minimis variance waived (still recorded)
    adjustments: dict[str, str] = {}           # {"ADJ_PRICE": "6.00", ...}
    correction: str | None = None              # Suggested correction for the supplier
    de_minimis: bool = False


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

    # Detail of the run
    header_findings: list[Finding] = []     # Currency, totals, receipt checks
    evidence: list[Evidence] = []           # Every evidence ID a finding may cite
    allocations: list[dict[str, str]] = []  # Allocations this run would record
    receipt_allocations: list[dict[str, str]] = []  # Accepted receipt qty claimed (3-way)
    mode: str = "2way"                      # "2way" or "3way"
    disposition: Disposition | None = None
    invoiced_total: Money | None = None
    approved_total: Money | None = None
    blocked_total: Money | None = None
    waived_total: Money | None = None

    def all_findings(self) -> list[Finding]:
        out = list(self.header_findings)
        for line in self.line_results:
            out.extend(line.findings)
        return out

    def not_evaluated_checks(self) -> list[str]:
        return sorted(
            {f.check for f in self.all_findings() if f.status == CheckStatus.CHECK_NOT_EVALUATED}
        )

    def evidence_map(self) -> dict[str, str]:
        return {e.evidence_id: e.normalized_value or e.raw_value for e in self.evidence}
