"""Builders for small team-schema (profile team-json-v1) EDI documents in tests."""

from __future__ import annotations

import json

BUYER, SUPPLIER = "BUYER-T", "SUP-T"


def _party(pid: str) -> dict:
    return {"id": pid, "name": pid}


def po(lines: list[tuple], number: str = "PO-T1", supplier: str = SUPPLIER) -> bytes:
    """lines: (line_number, sku, qty, uom, price)."""
    return json.dumps({
        "transaction_type": "850", "x12_version": "004010", "po_number": number,
        "po_date": "2026-10-01", "buyer": _party(BUYER), "supplier": _party(supplier),
        "currency": "USD",
        "line_items": [{"line_number": n, "buyer_sku": sku, "quantity": q,
                        "unit_of_measure": u, "unit_price": p} for n, sku, q, u, p in lines],
    }).encode()


def invoice(lines: list[tuple], number: str = "INV-T1", po_number: str = "PO-T1",
            total: str | None = None, supplier: str = SUPPLIER) -> bytes:
    """lines: (line_number, sku, qty, uom, price, line_total)."""
    from decimal import Decimal

    computed = sum(Decimal(t) for *_, t in lines)
    return json.dumps({
        "transaction_type": "810", "x12_version": "004010", "invoice_number": number,
        "invoice_date": "2026-10-20", "purchase_order_number": po_number,
        "buyer": _party(BUYER), "supplier": _party(supplier), "currency": "USD",
        "line_items": [{"line_number": n, "buyer_sku": sku, "invoiced_quantity": q,
                        "unit_of_measure": u, "unit_price": p, "line_total": t}
                       for n, sku, q, u, p, t in lines],
        "tax": "0", "shipping": "0", "invoice_total": total or str(computed),
    }).encode()


def receipt(lines: list[tuple], number: str = "RA-T1", po_number: str = "PO-T1",
            mode: str | None = None, event: str | None = None,
            date: str = "2026-10-15") -> bytes:
    """lines: (line_number, sku, received, accepted, rejected, uom)."""
    doc = {
        "transaction_type": "861", "x12_version": "004010", "receiving_advice_number": number,
        "receipt_date": date, "purchase_order_number": po_number,
        "buyer": _party(BUYER), "supplier": _party(SUPPLIER),
        "line_items": [{"line_number": n, "buyer_sku": sku, "received_quantity": rec,
                        "accepted_quantity": acc, "rejected_quantity": rej,
                        "unit_of_measure": u} for n, sku, rec, acc, rej, u in lines],
    }
    if mode:
        doc["receipt_quantity_mode"] = mode
    if event:
        doc["receipt_event_id"] = event
    return json.dumps(doc).encode()
