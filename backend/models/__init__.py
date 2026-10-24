"""
backend/models/__init__.py

Flat re-export of all public model names.

Teammates import from this namespace:
    from backend.models import PurchaseOrder, CaseResult, ReviewRequest, Money
"""

# Enums
# AI models
from backend.models.ai import AIExplanation

# Value types
# Supporting models
from backend.models.common import (
    AuditEvent,
    CheckStatus,
    DocumentStatus,
    Evidence,
    FindingStatus,
    Money,
    ReceiptEventType,
    ReviewActionType,
    ReviewStatus,
    TransactionType,
)

# Document models
from backend.models.documents import (
    Invoice,
    InvoiceLine,
    POLine,
    PurchaseOrder,
    ReceivingAdvice,
    ReceivingAdviceLine,
    StoredDocument,
)

# Reconciliation models
from backend.models.reconciliation import (
    BLOCKING_STATES,
    STATE_PRECEDENCE,
    CaseResult,
    Disposition,
    Finding,
    LineResult,
    ReconciliationPolicy,
)

# Reference models
from backend.models.reference import (
    PriceContract,
    ProductMaster,
    UOMConversion,
)

# Review models
from backend.models.review import (
    ReviewRecord,
    ReviewRequest,
)

__all__ = [
    # Enums
    "TransactionType",
    "DocumentStatus",
    "FindingStatus",
    "CheckStatus",
    "ReviewStatus",
    "ReviewActionType",
    "ReceiptEventType",
    # Value types
    "Money",
    # Supporting models
    "Evidence",
    "AuditEvent",
    # Document models
    "POLine",
    "PurchaseOrder",
    "InvoiceLine",
    "Invoice",
    "ReceivingAdviceLine",
    "ReceivingAdvice",
    "StoredDocument",
    # Reference models
    "UOMConversion",
    "PriceContract",
    "ProductMaster",
    # Reconciliation models
    "BLOCKING_STATES",
    "STATE_PRECEDENCE",
    "Disposition",
    "ReconciliationPolicy",
    "Finding",
    "LineResult",
    "CaseResult",
    # Review models
    "ReviewRequest",
    "ReviewRecord",
    # AI models
    "AIExplanation",
]
