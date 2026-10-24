"""
backend/models/ai.py

Structured AI explanation. Produced by backend/ai_resolver.py only after the
provider output has been validated against the case evidence bundle.
"""

from __future__ import annotations

from pydantic import BaseModel


class AIExplanation(BaseModel):
    """A validated, evidence-cited hypothesis for a human reviewer."""

    model_config = {"protected_namespaces": ()}  # allow the model_version field name

    status: str                 # "OK" | "AI_ABSTAIN" | "AI_UNAVAILABLE"
    finding_ids: list[str]
    possible_cause: str
    cited_evidence_ids: list[str]
    uncertainty_note: str
    missing_evidence: list[str]
    suggested_next_action: str
    model_version: str
    prompt_version: str
