"""
backend/models/common.py

Shared enums, value types, and supporting models used across all backend modules.
No float arithmetic is permitted anywhere in this file.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator, model_serializer, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TransactionType(StrEnum):
    """EDI transaction set types supported by this application."""

    EDI_850 = "EDI_850"  # Purchase Order
    EDI_810 = "EDI_810"  # Invoice
    EDI_861 = "EDI_861"  # Receiving Advice (P0: ingestion only; P2: reconciliation)


class DocumentStatus(StrEnum):
    """Result of ingestion validation for a single EDI document."""

    VALID = "VALID"
    QUARANTINED = "QUARANTINED"          # Malformed, unsupported version, or missing required field
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"  # Same business key, different content
    REPLAY_NOOP = "REPLAY_NOOP"          # Identical content hash — already processed


class FindingStatus(StrEnum):
    """
    Outcome of a deterministic reconciliation check for a line or case.

    NOT_EVALUATED and CHECK_FAILED are intentionally distinct values:
    - NOT_EVALUATED: a check was skipped because a prerequisite was missing or blocked.
    - CHECK_FAILED: a check ran and produced a definitive failure.
    These must never be treated as aliases.
    """

    MATCHED_2WAY = "MATCHED_2WAY"               # All applicable P0 checks pass
    MATCHED_3WAY = "MATCHED_3WAY"               # All P0 + P2 receipt checks pass (P2 only)
    PRICE_VARIANCE = "PRICE_VARIANCE"            # Normalized unit price differs beyond tolerance
    QUANTITY_VARIANCE = "QUANTITY_VARIANCE"      # Cumulative invoice qty exceeds PO authorization
    PARTIAL_BILLING = "PARTIAL_BILLING"          # Invoice qty below remaining PO balance; allocation recorded
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # Reference data missing, expired, or contradictory
    WAIT_DOCUMENT = "WAIT_DOCUMENT"              # PO or required document not found
    WAIT_RECEIPT = "WAIT_RECEIPT"               # P2 enabled; no valid receipt yet
    REVIEW_LINK = "REVIEW_LINK"                 # Ambiguous product or line match; analyst must select
    RECEIPT_VARIANCE = "RECEIPT_VARIANCE"        # Accepted receipt qty below invoice qty (P2)
    OVER_RECEIPT = "OVER_RECEIPT"               # Accepted receipt exceeds PO authorization (P2)
    RECEIPT_REVIEW = "RECEIPT_REVIEW"            # Ambiguous receipt status (P2)
    NOT_EVALUATED = "NOT_EVALUATED"              # Check skipped — NOT the same as CHECK_FAILED
    CHECK_FAILED = "CHECK_FAILED"               # Check ran and definitively failed
    TECHNICAL_HOLD = "TECHNICAL_HOLD"            # Internal error; safe retry required


class ReviewActionType(StrEnum):
    """Actions a human reviewer can take on a reconciliation recommendation."""

    APPROVE = "APPROVE"    # Accept the proposed next step; no payment release or external mutation
    REJECT = "REJECT"      # Reject the recommendation; case remains open
    ESCALATE = "ESCALATE"  # Assign to an accountable owner with a reason and next action


class ReceiptEventType(StrEnum):
    """Semantics of a receiving advice event (P2)."""

    INCREMENTAL = "INCREMENTAL"  # This event adds to the running total
    CUMULATIVE = "CUMULATIVE"    # This event is the current full snapshot; prior snapshots are superseded


# ---------------------------------------------------------------------------
# Money — deterministic currency value; never stores float
# ---------------------------------------------------------------------------

_TWO_PLACES = Decimal("0.01")


class Money(BaseModel):
    """
    Immutable currency value backed by Decimal.

    Accepts only str or Decimal at construction — float and int are rejected.
    This ensures JSON deserialisation failures are loud (at the model boundary)
    rather than silent (inside a calculation).
    """

    amount: Decimal

    model_config = {"frozen": True}

    @model_validator(mode="before")
    @classmethod
    def _coerce_string_input(cls, v: Any) -> Any:
        """
        Allow Money to be constructed from a plain string (e.g. from JSON roundtrip).
        A bare string "2.00" is treated as {"amount": "2.00"}.
        All other inputs pass through to field-level validation.
        """
        if isinstance(v, str):
            return {"amount": v}
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_float_and_int(cls, v: Any) -> Decimal:
        if isinstance(v, float):
            raise ValueError("Money does not accept float; use str or Decimal")
        if isinstance(v, int):
            raise ValueError("Money does not accept int; use str or Decimal")
        if isinstance(v, str):
            return Decimal(v)
        if isinstance(v, Decimal):
            return v
        raise ValueError(f"Money expects str or Decimal, got {type(v).__name__}")

    @classmethod
    def from_str(cls, s: str) -> "Money":
        """Parse a string and quantize to two decimal places using ROUND_HALF_UP."""
        d = Decimal(s).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        return cls(amount=d)

    # Arithmetic — all results stay in Decimal / Money; no float introduced
    def __add__(self, other: "Money") -> "Money":
        return Money(amount=self.amount + other.amount)

    def __sub__(self, other: "Money") -> "Money":
        return Money(amount=self.amount - other.amount)

    def __neg__(self) -> "Money":
        return Money(amount=-self.amount)

    def __abs__(self) -> "Money":
        return Money(amount=abs(self.amount))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            return self.amount == other.amount
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.amount)

    def __str__(self) -> str:
        return str(self.amount)

    def __repr__(self) -> str:
        return f"Money(amount={self.amount!r})"

    @model_serializer
    def _serialize(self) -> str:
        """Serialize to string so JSON output never contains a bare float."""
        return str(self.amount)


# ---------------------------------------------------------------------------
# Evidence — field-level pointer to a source value
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """
    A structured pointer to a single field value from a source document.
    Referenced by AI explainer responses to prove grounding.
    """

    evidence_id: str
    source_doc_id: str         # PO ID, invoice ID, contract ID, etc.
    field_name: str            # Canonical field name, e.g. "unit_price", "uom"
    raw_value: str             # Original value as it appeared in the source document
    normalized_value: str | None = None  # Value after normalization (e.g. after UOM conversion)


# ---------------------------------------------------------------------------
# AuditEvent — append-only record of every state transition
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """
    A single append-only audit record.  Every state transition, human action,
    and service call produces one AuditEvent.  Fields match PRD section 20.2.
    """

    event_id: str           # UUID; unique per event
    case_id: str            # Which reconciliation case this belongs to
    correlation_key: str    # Idempotency key; same action retry shares the same key
    actor: str              # Service name or reviewer identifier
    utc_timestamp: datetime # Must be timezone-aware UTC
    event_type: str         # e.g. "RECONCILIATION_RUN", "REVIEW_ACTION", "TECHNICAL_HOLD"

    before_state: str | None = None       # JSON-serialized previous case state
    after_state: str | None = None        # JSON-serialized new case state
    doc_hashes: dict[str, str] | None = None    # {doc_id: sha256_hex}
    source_pointers: list[str] | None = None    # Segment/field references from raw source
    findings: str | None = None           # JSON-serialized list[LineResult]
    reason: str | None = None             # Reviewer-provided reason (required for REVIEW_ACTION events)
    escalation_owner: str | None = None   # Required when action is ESCALATE
    policy_version: str | None = None     # Identifies the reconciliation policy used
