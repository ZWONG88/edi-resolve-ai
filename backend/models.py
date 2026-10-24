"""Dataclasses shared across the backend. Money and quantities are always Decimal."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class ResultState(str, Enum):
    """Closed result-state vocabulary (PRD section 19). Do not add states."""

    MATCHED_2WAY = "MATCHED_2WAY"
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"
    PARTIAL_BILLING = "PARTIAL_BILLING"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WAIT_DOCUMENT = "WAIT_DOCUMENT"
    REVIEW_LINK = "REVIEW_LINK"
    QUARANTINED = "QUARANTINED"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    TECHNICAL_HOLD = "TECHNICAL_HOLD"
    NOT_EVALUATED = "NOT_EVALUATED"
    AI_ABSTAIN = "AI_ABSTAIN"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"


class CheckStatus(str, Enum):
    """Outcome of a single check. NOT_EVALUATED is never a pass and never a failure."""

    PASSED = "CHECK_PASSED"
    FAILED = "CHECK_FAILED"
    NOT_EVALUATED = "CHECK_NOT_EVALUATED"


class ReviewStatus(str, Enum):
    """Human workflow status, tracked separately from the deterministic result state."""

    OPEN = "OPEN"
    RESOLUTION_APPROVED = "RESOLUTION_APPROVED"
    RECOMMENDATION_REJECTED = "RECOMMENDATION_REJECTED"
    ESCALATED = "ESCALATED"


# States that block a verified pass or a closing approval.
BLOCKING_STATES = {
    ResultState.QUARANTINED,
    ResultState.TECHNICAL_HOLD,
    ResultState.WAIT_DOCUMENT,
    ResultState.REVIEW_LINK,
    ResultState.INSUFFICIENT_EVIDENCE,
    ResultState.DUPLICATE_CONFLICT,
    ResultState.NOT_EVALUATED,
}

# Case-state precedence, most severe first.
STATE_PRECEDENCE = [
    ResultState.QUARANTINED,
    ResultState.DUPLICATE_CONFLICT,
    ResultState.TECHNICAL_HOLD,
    ResultState.WAIT_DOCUMENT,
    ResultState.REVIEW_LINK,
    ResultState.INSUFFICIENT_EVIDENCE,
    ResultState.NOT_EVALUATED,
    ResultState.QUANTITY_VARIANCE,
    ResultState.PRICE_VARIANCE,
    ResultState.PARTIAL_BILLING,
    ResultState.MATCHED_2WAY,
]


@dataclass(frozen=True)
class RawLineItem:
    """Exact source string values for one line, retained for provenance."""

    fields: dict[str, str]


@dataclass(frozen=True)
class LineItem:
    line_id: str
    product_id: str
    upc: str
    description: str
    quantity: Decimal
    uom: str
    unit_price: Decimal
    price_basis_qty: Decimal | None
    extended_price: Decimal | None
    raw: RawLineItem


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    po_date: str
    buyer: str
    supplier: str
    currency: str
    lines: list[LineItem]
    content_hash: str
    ingested_at: str
    raw_fields: dict[str, Any]
    doc_type: str = "850"


@dataclass(frozen=True)
class Invoice:
    invoice_number: str
    invoice_date: str
    po_number: str
    buyer: str
    supplier: str
    currency: str
    lines: list[LineItem]
    content_hash: str
    ingested_at: str
    raw_fields: dict[str, Any]
    total_amount: Decimal | None = None
    charges: list[dict[str, str]] = field(default_factory=list)
    doc_type: str = "810"


@dataclass(frozen=True)
class StoredDocument:
    """A document stored but not reconciled (e.g. an 861 while P2 is disabled)."""

    doc_type: str
    content_hash: str
    ingested_at: str
    raw_fields: dict[str, Any]


@dataclass(frozen=True)
class UOMConversion:
    product_id: str
    from_uom: str
    to_uom: str
    factor: Decimal
    effective_date: str
    expiry_date: str | None = None


@dataclass(frozen=True)
class PriceContract:
    contract_id: str
    product_id: str
    unit_price: Decimal
    uom: str
    effective_date: str
    expiry_date: str | None


@dataclass(frozen=True)
class TolerancePolicy:
    """Pinned per run (PRD 18.1). Zero tolerances in the prototype baseline."""

    price_abs: Decimal = Decimal("0")
    price_rel: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    version: str = "P0-baseline-v1"


@dataclass
class Finding:
    """Result of a single deterministic check on one line (or the header)."""

    finding_id: str
    check: str
    status: CheckStatus
    result_state: ResultState | None
    message: str
    po_value: str | None = None
    invoice_value: str | None = None
    normalized_po_value: str | None = None
    normalized_invoice_value: str | None = None
    delta: str | None = None
    tolerance_applied: str | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class DiscrepancyRecord:
    """All findings for one invoice line against its linked PO line."""

    case_id: str
    po_line: str | None
    invoice_line: str
    result_state: ResultState
    po_value_raw: dict[str, str] = field(default_factory=dict)
    invoice_value_raw: dict[str, str] = field(default_factory=dict)
    po_value_normalized: dict[str, str] = field(default_factory=dict)
    invoice_value_normalized: dict[str, str] = field(default_factory=dict)
    delta: dict[str, str] = field(default_factory=dict)
    tolerance_applied: str = ""
    evidence: list[str] = field(default_factory=list)
    not_evaluated_checks: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


@dataclass
class CaseResult:
    """Output of one deterministic comparison run."""

    result_state: ResultState
    records: list[DiscrepancyRecord]
    header_findings: list[Finding]
    evidence: dict[str, str]
    policy_version: str
    allocations: list[dict[str, str]] = field(default_factory=list)

    def all_findings(self) -> list[Finding]:
        out = list(self.header_findings)
        for rec in self.records:
            out.extend(rec.findings)
        return out

    def not_evaluated_checks(self) -> list[str]:
        return sorted(
            {f.check for f in self.all_findings() if f.status == CheckStatus.NOT_EVALUATED}
        )


@dataclass
class AIExplanation:
    status: str  # "OK", "AI_ABSTAIN" or "AI_UNAVAILABLE"
    finding_ids: list[str]
    possible_cause: str
    cited_evidence_ids: list[str]
    uncertainty_note: str
    missing_evidence: list[str]
    suggested_next_action: str
    model_version: str
    prompt_version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEvent:
    case_id: str | None
    event_type: str
    actor: str
    before_state: str | None = None
    after_state: str | None = None
    doc_hashes: list[str] = field(default_factory=list)
    reference_versions: dict[str, str] = field(default_factory=dict)
    calculations: dict[str, Any] = field(default_factory=dict)
    recommendation_version: str | None = None
    reviewer_reason: str | None = None
    escalation_owner: str | None = None
    idempotency_key: str | None = None
