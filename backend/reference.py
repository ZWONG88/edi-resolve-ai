"""Sole reader of data/reference/. The comparator never opens reference files itself.

File format (PR #1 backend plan): `uom_conversions.json` and `price_contracts.json` are
arrays of UOMConversion / PriceContract objects; `product_master.json` is an object
keyed by product_id. Numbers are JSON strings.

Missing, expired or conflicting records are surfaced (None, or ReconciliationError);
nothing is ever guessed or fetched from outside sources.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.exceptions import ReconciliationError
from backend.models import PriceContract, ProductMaster, UOMConversion

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"
UOM_FILE = "uom_conversions.json"
CONTRACT_FILE = "price_contracts.json"
PRODUCT_FILE = "product_master.json"


def _reject_number(value: str) -> Any:
    raise ReconciliationError(f"reference numbers must be JSON strings, got {value}")


def _read_json(path: Path, empty: Any) -> Any:
    if not path.exists():
        return empty
    return json.loads(path.read_bytes(), parse_float=_reject_number, parse_int=_reject_number)


def _validate(model: type, record: Any, where: str) -> Any:
    try:
        return model.model_validate(record)
    except ValidationError as exc:
        raise ReconciliationError(f"{where}: invalid reference record: {exc}") from exc


def load_uom_conversions(path: str | Path | None = None) -> list[UOMConversion]:
    """Load UOM conversions. A missing file means no factors are known."""
    path = Path(path) if path else REFERENCE_DIR / UOM_FILE
    records = _read_json(path, [])
    return [_validate(UOMConversion, r, f"{path.name}[{i}]") for i, r in enumerate(records)]


def load_price_contracts(path: str | Path | None = None) -> list[PriceContract]:
    """Load price contracts. A missing file means no contracts are known."""
    path = Path(path) if path else REFERENCE_DIR / CONTRACT_FILE
    records = _read_json(path, [])
    return [_validate(PriceContract, r, f"{path.name}[{i}]") for i, r in enumerate(records)]


def load_product_master(path: str | Path | None = None) -> dict[str, ProductMaster]:
    path = Path(path) if path else REFERENCE_DIR / PRODUCT_FILE
    records = _read_json(path, {})
    return {pid: _validate(ProductMaster, r, f"{path.name}[{pid}]") for pid, r in records.items()}


def _valid_on(effective: date, expiry: date | None, on_date: date | None) -> bool:
    if on_date is None:
        return True
    return effective <= on_date and (expiry is None or on_date <= expiry)


def _factors(
    records: list[UOMConversion], uom_from: str, uom_to: str, on_date: date | None
) -> set[Decimal]:
    factors: set[Decimal] = set()
    for rec in records:
        if not _valid_on(rec.effective_date, rec.expiry_date, on_date):
            continue
        pair = (rec.from_uom.upper(), rec.to_uom.upper())
        if pair == (uom_from, uom_to):
            factors.add(rec.factor)
        elif pair == (uom_to, uom_from):
            factors.add(Decimal("1") / rec.factor)
    return factors


def get_conversion(
    product_id: str,
    uom_from: str,
    uom_to: str,
    conversions: list[UOMConversion],
    on_date: date | None = None,
) -> Decimal | None:
    """Units of `uom_to` per one `uom_from`, or None when no valid record exists.

    Product-specific records take precedence over universal ones (product_id=None).
    Raises ReconciliationError when valid records at the same scope contradict.
    """
    uom_from, uom_to = uom_from.upper(), uom_to.upper()
    if uom_from == uom_to:
        return Decimal("1")
    for scope in (product_id, None):
        records = [c for c in conversions if c.product_id == scope]
        factors = _factors(records, uom_from, uom_to, on_date)
        if len(factors) > 1:
            raise ReconciliationError(
                f"{product_id}: conflicting {uom_from}->{uom_to} conversion records"
            )
        if factors:
            return factors.pop()
    return None


def get_active_contracts(
    product_id: str,
    as_of_date: date,
    contracts: list[PriceContract],
    supplier_id: str | None = None,
    currency: str | None = None,
) -> list[PriceContract]:
    """Contracts valid on the date for this product, partner scope and currency."""
    return [
        c
        for c in contracts
        if c.product_id == product_id
        and _valid_on(c.effective_from, c.effective_to, as_of_date)
        and (c.partner_scope is None or supplier_id is None or c.partner_scope == supplier_id)
        and (currency is None or c.currency == currency)
    ]


def get_contract_price(
    product_id: str,
    as_of_date: date,
    contracts: list[PriceContract],
    supplier_id: str | None = None,
    currency: str | None = None,
) -> PriceContract | None:
    """The single active contract, None if none. Overlapping contracts raise."""
    active = get_active_contracts(product_id, as_of_date, contracts, supplier_id, currency)
    if len(active) > 1:
        ids = ", ".join(c.contract_id for c in active)
        raise ReconciliationError(f"{product_id}: overlapping contracts in validity ({ids})")
    return active[0] if active else None


def reference_version(directory: str | Path | None = None) -> str:
    """Short hash of the reference files, pinned on every run for audit."""
    directory = Path(directory) if directory else REFERENCE_DIR
    digest = hashlib.sha256()
    for name in (UOM_FILE, CONTRACT_FILE, PRODUCT_FILE):
        path = directory / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()[:12]
