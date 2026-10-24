"""
backend/models/__init__.py

Flat re-export of all public model names.

Teammates import from this namespace:
    from backend.models import PurchaseOrder, CaseResult, ReviewRequest, Money
"""

# Enums
from backend.models.common import (
    TransactionType,
    DocumentStatus,
    FindingStatus,
    ReviewActionType,
    ReceiptEventType,
)

# Value types
from backend.models.common import Money

# Supporting models
from backend.models.common import Evidence, AuditEvent

# Document models
from backend.models.documents import (
    POLine,
    PurchaseOrder,
    InvoiceLine,
    Invoice,
    ReceivingAdviceLine,
    ReceivingAdvice,
)

# Reference models
from backend.models.reference import (
    UOMConversion,
    PriceContract,
    ProductMaster,
)

# Reconciliation models
from backend.models.reconciliation import (
    ReconciliationPolicy,
    LineResult,
    CaseResult,
)

# Review models
from backend.models.review import (
    ReviewRequest,
    ReviewRecord,
)

__all__ = [
    # Enums
    "TransactionType",
    "DocumentStatus",
    "FindingStatus",
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
    # Reference models
    "UOMConversion",
    "PriceContract",
    "ProductMaster",
    # Reconciliation models
    "ReconciliationPolicy",
    "LineResult",
    "CaseResult",
    # Review models
    "ReviewRequest",
    "ReviewRecord",
]
