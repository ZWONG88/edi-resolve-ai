"""Sole reader of data/reference/. The comparator never opens reference files itself.

Missing, expired or conflicting records are surfaced (None or ReconciliationError);
nothing is ever guessed or fetched from outside sources.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from backend.exceptions import ReconciliationError
from backend.models import PriceContract, UOMConversion

REFERENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"
UOM_FILE = "uom_conversions.json"
CONTRACT_FILE = "price_contracts.json"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ReconciliationError(f"{path.name}: root must be an object")
    return data


def _dec(value: object, where: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ReconciliationError(f"{where}: invalid decimal {value!r}") from exc
    if not isinstance(value, str):
        raise ReconciliationError(f"{where}: numeric values must be strings")
    return result


def load_uom_conversions(path: str | Path | None = None) -> dict[str, list[UOMConversion]]:
    """Load UOM conversions. A missing file yields an empty mapping (no factors known)."""
    path = Path(path) if path else REFERENCE_DIR / UOM_FILE
    out: dict[str, list[UOMConversion]] = {}
    for product_id, records in _read_json(path).items():
        out[product_id] = [
            UOMConversion(
                product_id=product_id,
                from_uom=r["from_uom"].upper(),
                to_uom=r["to_uom"].upper(),
                factor=_dec(r["factor"], f"{product_id}.factor"),
                effective_date=r["effective_date"],
                expiry_date=r.get("expiry_date"),
            )
            for r in records
        ]
    return out


def load_price_contracts(path: str | Path | None = None) -> dict[str, list[PriceContract]]:
    """Load price contracts. A missing file yields an empty mapping."""
    path = Path(path) if path else REFERENCE_DIR / CONTRACT_FILE
    out: dict[str, list[PriceContract]] = {}
    for product_id, records in _read_json(path).items():
        out[product_id] = [
            PriceContract(
                contract_id=r["contract_id"],
                product_id=product_id,
                unit_price=_dec(r["unit_price"], f"{r['contract_id']}.unit_price"),
                uom=r["uom"].upper(),
                effective_date=r["effective_date"],
                expiry_date=r.get("expiry_date"),
            )
            for r in records
        ]
    return out


def _valid_on(effective: str, expiry: str | None, on_date: str | None) -> bool:
    if on_date is None:
        return True
    return effective <= on_date and (expiry is None or on_date <= expiry)


def get_conversion(
    product_id: str,
    uom_from: str,
    uom_to: str,
    conversions: dict[str, list[UOMConversion]],
    on_date: str | None = None,
) -> Decimal | None:
    """Units of `uom_to` per one `uom_from`. None when no valid record exists.

    Raises ReconciliationError when valid records contradict each other.
    """
    uom_from, uom_to = uom_from.upper(), uom_to.upper()
    if uom_from == uom_to:
        return Decimal("1")
    factors: set[Decimal] = set()
    for rec in conversions.get(product_id, []):
        if not _valid_on(rec.effective_date, rec.expiry_date, on_date):
            continue
        if rec.factor <= 0:
            raise ReconciliationError(f"{product_id}: non-positive conversion factor")
        if (rec.from_uom, rec.to_uom) == (uom_from, uom_to):
            factors.add(rec.factor)
        elif (rec.from_uom, rec.to_uom) == (uom_to, uom_from):
            factors.add(Decimal("1") / rec.factor)
    if len(factors) > 1:
        raise ReconciliationError(
            f"{product_id}: conflicting {uom_from}->{uom_to} conversion records"
        )
    return factors.pop() if factors else None


def get_contract_price(
    product_id: str, effective_date: str, contracts: dict[str, list[PriceContract]]
) -> PriceContract | None:
    """The contract valid on `effective_date`, None if none. Overlaps raise."""
    valid = [
        c
        for c in contracts.get(product_id, [])
        if _valid_on(c.effective_date, c.expiry_date, effective_date)
    ]
    if len(valid) > 1:
        ids = ", ".join(c.contract_id for c in valid)
        raise ReconciliationError(f"{product_id}: overlapping contracts in validity ({ids})")
    return valid[0] if valid else None


def reference_version(directory: str | Path | None = None) -> str:
    """Short hash of the reference files, pinned on every run for audit."""
    directory = Path(directory) if directory else REFERENCE_DIR
    digest = hashlib.sha256()
    for name in (UOM_FILE, CONTRACT_FILE):
        path = directory / name
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.exists() else b"<missing>")
    return digest.hexdigest()[:12]
