"""EDI 820 remittance payload (team rules doc, Rule 5.2).

Generated for invoices whose disposition allows payment. The 820 is a file the AP
team can review or hand to their payment system; this application never transmits
it and never releases funds. Paid = invoiced total - blocked cash, with line-level
adjustment reason codes (ADJ_PRICE, ADJ_SHORTAGE, ADJ_UNMAPPED, ADJ_EXTENSION).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.models import CaseResult, Disposition, Invoice
from backend.money import fmt_money

PAYABLE = {Disposition.AUTO_APPROVED, Disposition.APPROVED_WITH_SHORT_PAY,
           Disposition.NEEDS_HUMAN_AP_REVIEW}
STATUS = "GENERATED_NOT_TRANSMITTED"


def can_generate(result: CaseResult, human_approved: bool = False) -> bool:
    """AUTO_APPROVED / SHORT_PAY automatically; NEEDS_HUMAN_AP_REVIEW only after approval."""
    if result.disposition in (Disposition.AUTO_APPROVED, Disposition.APPROVED_WITH_SHORT_PAY):
        return result.blocked_total is not None
    return result.disposition == Disposition.NEEDS_HUMAN_AP_REVIEW and human_approved


def build_820(result: CaseResult, invoice: Invoice, case_id: str,
              evidence_version: int) -> dict[str, Any]:
    if result.disposition not in PAYABLE or result.blocked_total is None:
        raise ValueError(f"no remittance for disposition {result.disposition}")
    invoiced = result.invoiced_total.amount
    blocked = result.blocked_total.amount
    header_adjustment = blocked - sum(
        (ln.blocked_amount.amount for ln in result.line_results if ln.blocked_amount),
        Decimal("0"),
    )
    lines = []
    for ln in result.line_results:
        if ln.invoiced_amount is None:
            continue
        lines.append({
            "line_number": ln.invoice_line_id,
            "buyer_sku": ln.invoice_value_raw.get("product_id", ""),
            "invoiced_amount": fmt_money(ln.invoiced_amount.amount),
            "approved_amount": fmt_money(ln.approved_amount.amount),
            "adjustment_amount": fmt_money(ln.blocked_amount.amount),
            "adjustments": [{"reason_code": code, "amount": amount}
                            for code, amount in ln.adjustments.items()]
            if ln.blocked_amount.amount else [],
            "de_minimis_waived": fmt_money(ln.waived_amount.amount) if ln.de_minimis else None,
            "note": ln.correction if ln.blocked_amount.amount else None,
        })
    return {
        "transaction_type": "820",
        "x12_version": "004010",
        "status": STATUS,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "payer": {"id": invoice.buyer_id},
        "payee": {"id": invoice.supplier_id},
        "currency": invoice.currency,
        "invoice_number": invoice.invoice_id,
        "invoice_date": invoice.invoice_date.isoformat(),
        "purchase_order_number": invoice.po_references[0],
        "disposition": result.disposition.value,
        "invoiced_amount": fmt_money(invoiced),
        "adjustment_amount": fmt_money(blocked),
        "paid_amount": fmt_money(invoiced - blocked),
        "header_adjustment": fmt_money(header_adjustment) if header_adjustment else None,
        "line_adjustments": lines,
        "case_id": case_id,
        "evidence_version": evidence_version,
        "policy_version": result.policy_version,
    }


def target_path(payload: dict[str, Any], directory: str | Path) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in payload["invoice_number"])
    return Path(directory) / f"820_{safe}_v{payload['evidence_version']}.json"


def write_820(payload: dict[str, Any], directory: str | Path) -> Path:
    path = target_path(payload, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
