"""Backend exception types."""

from __future__ import annotations


class ParseError(Exception):
    """A source document failed validation. Names the exact source field."""

    def __init__(self, field_name: str, raw_value: object, message: str) -> None:
        self.field_name = field_name
        self.raw_value = raw_value
        self.message = message
        super().__init__(f"{field_name}: {message} (value={raw_value!r})")


class ReconciliationError(Exception):
    """Reference or rules evidence is contradictory or the rules engine failed."""


class AuditPersistenceError(Exception):
    """An audit write failed; the surrounding action was rolled back."""


class StaleVersionError(Exception):
    """A review action targeted an out-of-date case or recommendation version."""


class ReviewValidationError(Exception):
    """A review action was refused (missing reason, missing owner, blocked closure)."""
