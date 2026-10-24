"""Cumulative PO allocation lookups (AT08)."""

from __future__ import annotations

import sqlite3
from decimal import Decimal


def get_allocated_quantity(
    po_id: str, line_id: str, conn: sqlite3.Connection, exclude_invoice_id: str | None = None
) -> Decimal:
    """Sum of active base-UOM allocations against a PO line from other invoices.

    Excluding the current invoice means a rerun or restart never double counts.
    """
    rows = conn.execute(
        "SELECT quantity FROM allocations WHERE po_id = ? AND po_line = ? AND active = 1"
        " AND invoice_id IS NOT ?",
        (po_id, line_id, exclude_invoice_id),
    ).fetchall()
    return sum((Decimal(r["quantity"]) for r in rows), Decimal("0"))


def get_prior_allocations(
    po_id: str, line_ids: list[str], conn: sqlite3.Connection, exclude_invoice_id: str | None
) -> dict[str, Decimal]:
    return {
        line_id: get_allocated_quantity(po_id, line_id, conn, exclude_invoice_id)
        for line_id in line_ids
    }
