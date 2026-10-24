"""Money rounding shared by the rules engine and the disposition layer."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def round_money(value: Decimal) -> Decimal:
    """Round to cents with ROUND_HALF_UP. Use only at line extension / totals."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal) -> str:
    return str(value.quantize(CENT))
