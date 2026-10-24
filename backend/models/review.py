"""
backend/models/review.py

Pydantic models for the human review workflow.

ReviewRequest: what a reviewer submits (action + reason + optional escalation owner).
ReviewRecord:  what is persisted after a successful atomic write to the audit store.

Key constraints:
- reason must be non-empty (whitespace-only is rejected).
- escalation_owner is required when action == ESCALATE.
- case_version is used for optimistic locking: the orchestrator rejects a
  ReviewRequest whose case_version does not match the current persisted version.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_validator, model_validator

from backend.models.common import ReviewActionType


class ReviewRequest(BaseModel):
    """Submitted by a human reviewer to approve, reject, or escalate a recommendation."""

    case_id: str
    case_version: int          # Must be > 0; used for optimistic lock check in orchestrator
    action: ReviewActionType
    reason: str                # Non-empty after stripping whitespace
    escalation_owner: str | None = None  # Required when action == ESCALATE

    @field_validator("case_version")
    @classmethod
    def _version_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("case_version must be >= 1")
        return v

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must be a non-empty string")
        return v

    @model_validator(mode="after")
    def _escalate_requires_owner(self) -> "ReviewRequest":
        if self.action == ReviewActionType.ESCALATE:
            if not self.escalation_owner or not self.escalation_owner.strip():
                raise ValueError("escalation_owner is required for ESCALATE actions")
        return self


class ReviewRecord(BaseModel):
    """
    Persisted record of a completed human review action.
    Written atomically alongside its AuditEvent in audit_db.save_review_action().
    """

    review_id: str              # UUID
    case_id: str
    case_version: int           # Version at time of review
    action: ReviewActionType
    reason: str
    escalation_owner: str | None = None
    utc_timestamp: datetime     # UTC
    audit_event_id: str         # FK to the AuditEvent written in the same transaction
