"""Synthetic master catalogue: enrichment only, never a rules input (PRD 17.3)."""

from __future__ import annotations

import json
import shutil
from decimal import Decimal

import pytest

from backend import db, orchestrator
from backend.reference import (
    CATALOG_FILE,
    REFERENCE_DIR,
    load_master_catalog,
    lookup_catalog_product,
    reference_version,
)
from tests.conftest import DATASETS, REFERENCE

CATALOG = load_master_catalog(REFERENCE / CATALOG_FILE)


def test_real_catalogue_loads_with_exact_decimals() -> None:
    raw = json.loads((REFERENCE_DIR / CATALOG_FILE).read_text())
    assert raw["metadata"]["synthetic"] is True
    catalog = load_master_catalog()
    products = {p.catalog_product_id: p for p in catalog.values() if p is not None}
    assert len(products) == len(raw["products"]) - 2  # SKU2030 pair is ambiguous
    prices = [r.unit_price for p in products.values() for r in p.price_records]
    assert prices and all(type(v) is Decimal for v in prices)


def test_lookup_by_partner_and_sku() -> None:
    product = lookup_catalog_product(CATALOG, "BUYER001", "SUP002", "SKU2001")
    assert product.product_name == "Fresh Market Product 1"
    assert product.price_records[0].unit_price == Decimal("6.39")
    assert lookup_catalog_product(CATALOG, "BUYER001", "SUP002", "NOPE") is None
    assert lookup_catalog_product(CATALOG, "OTHER", "SUP002", "SKU2001") is None


def test_conflicting_sku_pair_is_ambiguous() -> None:
    assert ("BUYER001", "SUP002", "SKU2030") in CATALOG
    assert lookup_catalog_product(CATALOG, "BUYER001", "SUP002", "SKU2030") is None


def test_random_price_and_inferred_pack_are_flagged() -> None:
    product = lookup_catalog_product(CATALOG, "DEMO-BUYER-01", "DEMO-SUPPLIER-01", "SKU0001")
    usd = next(r for r in product.price_records if r.currency == "USD")
    assert usd.unit_price == Decimal("7.44")
    assert usd.selection.method == "random_within_range" and usd.selection.is_synthetic
    eur = next(r for r in product.price_records if r.currency == "EUR")
    assert not eur.selection.is_synthetic
    assert product.pack_size == 6 and product.pack_size_origin.startswith("synthetic")


def test_missing_catalogue_is_empty(tmp_path) -> None:
    assert load_master_catalog(tmp_path / "nope.json") == {}


def test_catalogue_not_in_reference_version(tmp_path) -> None:
    for name in ("uom_conversions.json", "price_contracts.json"):
        shutil.copy(REFERENCE / name, tmp_path / name)
    before = reference_version(tmp_path)
    shutil.copy(REFERENCE / CATALOG_FILE, tmp_path / CATALOG_FILE)
    assert reference_version(tmp_path) == before


@pytest.fixture
def two_refs(tmp_path):
    bare, with_catalog = tmp_path / "bare", tmp_path / "with_catalog"
    shutil.copytree(DATASETS / "reference", bare)
    shutil.copytree(DATASETS / "reference", with_catalog)
    shutil.copy(REFERENCE_DIR / CATALOG_FILE, with_catalog / CATALOG_FILE)
    return bare, with_catalog


def test_catalogue_does_not_change_outcomes(monkeypatch, tmp_path, two_refs) -> None:
    """Isolation guard: Dataset 2 reconciles identically with and without the catalogue."""
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    results = []
    for ref in two_refs:
        conn = db.init_db(tmp_path / f"{ref.name}.db")
        summary = orchestrator.import_dataset(conn, DATASETS / "dataset2", reference_dir=ref)
        rows = sorted((r["state"], r["disposition"], r["invoiced"], r["approved"], r["blocked"])
                      for r in summary["cases"])
        results.append((rows, summary["by_disposition"], summary["totals"]))
        conn.close()
    assert results[0] == results[1]


def test_catalog_enrichment_for_case(monkeypatch, conn) -> None:
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")
    summary = orchestrator.import_dataset(conn, DATASETS / "dataset3",
                                          reference_dir=DATASETS / "reference")
    case_id = summary["cases"][0]["case_id"]
    found = orchestrator.catalog_enrichment(conn, case_id, ["SKU2001", "SKU2030", "NOPE"],
                                            reference_dir=REFERENCE)
    assert set(found) == {"SKU2001"}
    assert found["SKU2001"]["prices"][0] == {"currency": "USD", "uom": "EA",
                                             "unit_price": "6.39", "synthetic": False}
    assert orchestrator.catalog_enrichment(conn, "missing-case", ["SKU2001"]) == {}
