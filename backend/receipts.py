"""Receipt ledger for three-way matching (PRD 18.3, rules doc Rule 3.3).

Builds, per PO line, the accepted quantity that receiving evidence supports:
- incremental events are summed; cumulative snapshots: only the latest counts and the
  earlier ones are kept as superseded;
- an exact replay (same content hash or same receipt event) is ignored;
- undeclared semantics with more than one event, or contradictory item/UOM, make the
  line ambiguous (INSUFFICIENT_EVIDENCE) instead of guessing.
Accepted quantity (not received) is what may be billed: rejected or damaged goods
are surfaced for receiving review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backend.models import ReceiptEventType, ReceivingAdvice, ReceivingAdviceLine

ZERO = Decimal("0")


@dataclass
class ReceiptLineTotal:
    line_id: str
    product_id: str
    uom: str
    accepted: Decimal = ZERO
    received: Decimal = ZERO
    rejected: Decimal = ZERO
    damaged: Decimal = ZERO
    receipt_ids: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)


@dataclass
class ReceiptLedger:
    receipt_ids: list[str] = field(default_factory=list)
    lines: dict[str, ReceiptLineTotal] = field(default_factory=dict)
    ambiguous: dict[str, str] = field(default_factory=dict)  # line_id -> reason

    @property
    def empty(self) -> bool:
        return not self.receipt_ids


def _review_reasons(line: ReceivingAdviceLine) -> list[str]:
    reasons = []
    if line.rejected_qty > 0:
        reasons.append(f"rejected {line.rejected_qty}")
    if line.damaged_qty > 0:
        reasons.append(f"damaged {line.damaged_qty}")
    if line.rejection_reason:
        reasons.append(f"reason {line.rejection_reason}")
    if line.receiving_condition and line.receiving_condition.upper() != "ACCEPTED":
        reasons.append(f"condition {line.receiving_condition}")
    if line.condition_review_required:
        reasons.append("receiving review requested")
    return reasons


def build_ledger(receipts: list[ReceivingAdvice]) -> ReceiptLedger:
    ledger = ReceiptLedger()
    seen: set[str] = set()
    unique: list[ReceivingAdvice] = []
    for receipt in sorted(receipts, key=lambda r: (r.receipt_date, r.receipt_id)):
        keys = {receipt.content_hash}
        if receipt.receipt_event_id:
            keys.add(f"event:{receipt.receipt_id}:{receipt.receipt_event_id}")
        if keys & seen:
            continue  # replay of an event already counted
        seen |= keys
        unique.append(receipt)
        ledger.receipt_ids.append(receipt.receipt_id)

    events: dict[str, list[tuple[ReceivingAdvice, ReceivingAdviceLine]]] = {}
    for receipt in unique:
        for line in receipt.lines:
            events.setdefault(line.line_id, []).append((receipt, line))

    for line_id, items in events.items():
        first = items[0][1]
        products = {ln.product_id for _, ln in items}
        uoms = {ln.uom.upper() for _, ln in items}
        modes = {ln.receipt_event_type for _, ln in items}
        if len(products) > 1 or len(uoms) > 1:
            ledger.ambiguous[line_id] = "receipt events disagree on item or unit of measure"
            continue
        if len(items) > 1 and (len(modes) > 1 or None in modes):
            ledger.ambiguous[line_id] = (
                "several receipt events without declared incremental/cumulative semantics"
            )
            continue
        total = ReceiptLineTotal(line_id=line_id, product_id=first.product_id,
                                 uom=first.uom.upper())
        if modes == {ReceiptEventType.CUMULATIVE} and len(items) > 1:
            *older, (latest_receipt, latest) = items
            total.superseded = [r.receipt_id for r, _ in older]
            items = [(latest_receipt, latest)]
        for receipt, line in items:
            total.accepted += line.accepted_qty
            total.received += line.received_qty
            total.rejected += line.rejected_qty
            total.damaged += line.damaged_qty
            total.receipt_ids.append(receipt.receipt_id)
            total.review_reasons.extend(_review_reasons(line))
        ledger.lines[line_id] = total
    return ledger
