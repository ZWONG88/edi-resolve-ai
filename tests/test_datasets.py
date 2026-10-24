"""Team datasets: frozen per-category outcomes (PRD 22.2 — report every category).

Dataset 1 is a clean 50-line PO/invoice/receipt. Datasets 2 and 3 contain intentional
discrepancies; these expectations were reviewed against the data and frozen.
"""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

import pytest

from backend import db, orchestrator
from tests.conftest import DATASETS

REFERENCE = DATASETS / "reference"


@pytest.fixture(autouse=True)
def _three_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")


def load(conn, name: str) -> dict:
    return orchestrator.import_dataset(conn, DATASETS / name, reference_dir=REFERENCE)


def test_dataset1_clean_auto_approved(conn) -> None:
    summary = load(conn, "dataset1")
    assert summary["by_disposition"] == {"AUTO_APPROVED": 1}
    row = summary["cases"][0]
    assert row["state"] == "MATCHED_3WAY"
    assert (Decimal(row["approved"]), Decimal(row["blocked"])) == (Decimal("36673.98"), 0)
    remit = orchestrator.current_remittance(conn, row["case_id"])
    assert remit["paid_amount"] == "36673.98"


def test_dataset2_categories(conn) -> None:
    summary = load(conn, "dataset2")
    assert summary["counts"] == {"850": 60, "861": 55, "810": 60}
    assert summary["by_disposition"] == {
        "AUTO_APPROVED": 30, "APPROVED_WITH_SHORT_PAY": 10,
        "NEEDS_HUMAN_AP_REVIEW": 10, "HOLD": 10,
    }
    states = Counter((r["state"], r["disposition"]) for r in summary["cases"])
    assert states == Counter({
        ("MATCHED_3WAY", "AUTO_APPROVED"): 20,         # 10 clean + 10 CS/EA normalized
        ("PARTIAL_BILLING", "AUTO_APPROVED"): 5,
        ("PRICE_VARIANCE", "APPROVED_WITH_SHORT_PAY"): 5,  # price overcharges
        ("PRICE_VARIANCE", "AUTO_APPROVED"): 5,            # header +$1.00 waived (4.1)
        ("RECEIPT_VARIANCE", "APPROVED_WITH_SHORT_PAY"): 5,
        ("OVER_RECEIPT", "NEEDS_HUMAN_AP_REVIEW"): 5,
        ("RECEIPT_REVIEW", "NEEDS_HUMAN_AP_REVIEW"): 5,   # damaged packaging
        ("INSUFFICIENT_EVIDENCE", "HOLD"): 5,            # currency mismatch
        ("WAIT_RECEIPT", "HOLD"): 5,                     # no 861
    })
    totals = {k: Decimal(v) for k, v in summary["totals"].items()}
    assert totals == {"invoiced": Decimal("23614.20"), "approved": Decimal("19106.30"),
                      "blocked": Decimal("524.80"), "held": Decimal("3983.10")}


def test_dataset3_flags_every_seeded_discrepancy(conn) -> None:
    summary = load(conn, "dataset3")
    row = summary["cases"][0]
    assert (row["state"], row["disposition"]) == ("RECEIPT_REVIEW", "NEEDS_HUMAN_AP_REVIEW")
    assert Decimal(row["blocked"]) == Decimal("382.00")
    result = json.loads(db.get_case(conn, row["case_id"])["result_json"])
    flagged = {ln["invoice_line_id"]: ln["status"] for ln in result["line_results"]
               if ln["status"] != "MATCHED_3WAY"}
    assert flagged == {
        "7": "QUANTITY_VARIANCE", "8": "RECEIPT_VARIANCE", "12": "PRICE_VARIANCE",
        "18": "UNMAPPED_LINE_ITEM", "20": "RECEIPT_REVIEW", "25": "PRICE_VARIANCE",
        "42": "OVER_RECEIPT",
    }
    blocked = {ln["invoice_line_id"]: ln["adjustments"] for ln in result["line_results"]
               if ln["adjustments"]}
    assert blocked["12"] == {"ADJ_PRICE": "70.50"}
    assert blocked["18"] == {"ADJ_UNMAPPED": "226.40"}
    assert blocked["25"] == {"ADJ_EXTENSION": "30.00"}
    totals = [f for f in result["header_findings"] if f["check"] == "invoice_total"][0]
    assert totals["delta"] == "-263.10"  # header total below the sum of lines


def test_reimport_is_idempotent(conn) -> None:
    load(conn, "dataset1")
    again = load(conn, "dataset1")
    assert [r["outcome"] for r in again["cases"]] == ["REPLAY_NOOP"]
    assert len(orchestrator.list_cases(conn)) == 1
