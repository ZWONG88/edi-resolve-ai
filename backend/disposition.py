"""Dollar impact and invoice disposition (team rules doc, Stages 3-5). Decimal only.

Per line:
    payable_qty   = min(billed, PO remaining, accepted receipt available [3-way])
    payable_price = min(invoice price, PO price)          (normalized base UOM)
    approved      = round_money(payable_qty x payable_price), never above invoiced
    blocked       = invoiced line amount - approved
This one formula equals the doc's Price Risk + Shortage Risk without double counting.
A line whose blocked amount is within the de minimis tolerance is waived (approved in
full) but the actual variance is kept (`waived_amount`, PRD 18.1).
"""

from __future__ import annotations

from decimal import Decimal

from backend.models import BLOCKING_STATES, Disposition, FindingStatus, Money
from backend.money import fmt_money, round_money

ZERO = Decimal("0")

# Receiving problems a person must confirm; dollars are still computed on accepted qty.
REVIEW_STATES = {FindingStatus.RECEIPT_REVIEW, FindingStatus.OVER_RECEIPT}


def _qty(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else str(value)


def line_impact(
    *,
    billed_qty: Decimal,
    inv_price: Decimal,
    po_price: Decimal,
    invoiced_amount: Decimal,
    po_remaining: Decimal,
    accepted_available: Decimal | None,
    uom: str,
    de_minimis: Decimal,
) -> dict:
    """Approved / blocked amounts, adjustment codes and a suggested correction."""
    caps = [billed_qty, max(po_remaining, ZERO)]
    if accepted_available is not None:
        caps.append(max(accepted_available, ZERO))
    payable_qty = min(caps)
    payable_price = min(inv_price, po_price)
    approved = min(round_money(payable_qty * payable_price), invoiced_amount)
    blocked = invoiced_amount - approved

    price_adj = round_money(billed_qty * (inv_price - payable_price))
    shortage_adj = round_money((billed_qty - payable_qty) * payable_price)
    other_adj = blocked - price_adj - shortage_adj
    adjustments = {
        code: fmt_money(amount)
        for code, amount in (("ADJ_PRICE", price_adj), ("ADJ_SHORTAGE", shortage_adj),
                             ("ADJ_EXTENSION", other_adj))
        if amount != 0
    }
    notes = []
    if price_adj:
        notes.append(f"bill at PO price ${po_price}/{uom} instead of ${inv_price} "
                     f"(reduce ${fmt_money(price_adj)})")
    if shortage_adj:
        basis = "accepted at receiving" if accepted_available is not None and \
            payable_qty == max(accepted_available, ZERO) else "remaining on the PO"
        notes.append(f"bill {_qty(payable_qty)} {uom} {basis} instead of {_qty(billed_qty)} "
                     f"(reduce ${fmt_money(shortage_adj)})")
    if other_adj:
        notes.append(f"correct the line total to ${fmt_money(approved + price_adj + shortage_adj)}"
                     f" (difference ${fmt_money(other_adj)})")

    waived = ZERO
    if ZERO < blocked <= de_minimis:
        waived, approved, blocked = blocked, invoiced_amount, ZERO
    return {
        "payable_qty": payable_qty,
        "invoiced_amount": Money(amount=invoiced_amount),
        "approved_amount": Money(amount=approved),
        "blocked_amount": Money(amount=blocked),
        "waived_amount": Money(amount=waived),
        "adjustments": adjustments,
        "correction": ("Suggested correction: " + "; ".join(notes) + ".") if notes else None,
        "de_minimis": waived > 0,
    }


def unmapped_impact(invoiced_amount: Decimal, line_id: str, product_id: str) -> dict:
    """Rule 3.1: a line not on the PO is approved at $0.00; 100% is blocked."""
    return {
        "payable_qty": ZERO,
        "invoiced_amount": Money(amount=invoiced_amount),
        "approved_amount": Money(amount=ZERO),
        "blocked_amount": Money(amount=invoiced_amount),
        "waived_amount": Money(amount=ZERO),
        "adjustments": {"ADJ_UNMAPPED": fmt_money(invoiced_amount)},
        "correction": f"Suggested correction: remove line {line_id} ({product_id}); it is not "
        f"on the purchase order (reduce ${fmt_money(invoiced_amount)}).",
        "de_minimis": False,
    }


def classify(
    case_state: FindingStatus,
    mode: str,
    blocked_total: Decimal | None,
    high_value_threshold: Decimal,
) -> Disposition:
    """Rule 5.1 / 4.2, preceded by the PRD holds the rules doc does not cover."""
    if case_state == FindingStatus.QUARANTINED:
        return Disposition.SCHEMA_VALIDATION_ERROR
    if case_state == FindingStatus.DUPLICATE_CONFLICT:
        return Disposition.DUPLICATE_INVOICE_REJECTED
    if case_state == FindingStatus.WAIT_DOCUMENT:
        return Disposition.PO_HEADER_MISMATCH
    if blocked_total is None or (case_state in BLOCKING_STATES
                                 and case_state not in REVIEW_STATES):
        return Disposition.HOLD
    if mode != "3way":
        return Disposition.RECEIPT_NOT_VERIFIED
    if case_state in REVIEW_STATES:
        return Disposition.NEEDS_HUMAN_AP_REVIEW
    if blocked_total == 0:
        return Disposition.AUTO_APPROVED
    if blocked_total > high_value_threshold:
        return Disposition.NEEDS_HUMAN_AP_REVIEW
    return Disposition.APPROVED_WITH_SHORT_PAY
