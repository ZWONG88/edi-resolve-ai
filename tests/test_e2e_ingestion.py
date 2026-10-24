"""Ingestion contract gaps not covered by AT09-AT11/AT21/AT23 (PRD FR-01, FR-02).

PO-side replay/conflict/quarantine, every ingest_receipt branch and dataset-import
skipping, all through orchestrator against an isolated database.
"""

from __future__ import annotations

import json

import pytest

from backend import db, orchestrator
from tests import team_docs as t
from tests.conftest import REFERENCE, fx


def doc_rows(conn, doc_type: str) -> list[str]:
    return [r["validation_status"] for r in conn.execute(
        "SELECT validation_status FROM ingested_documents WHERE doc_type = ?"
        " ORDER BY ingested_at, rowid", (doc_type,))]


def test_valid_pair_and_po_event_has_no_case(conn, run) -> None:
    """FR-01: both documents VALID; the PO's ingest event predates any case."""
    out = run("inv_AT01.json", "po_AT01.json")
    assert (doc_rows(conn, "850"), doc_rows(conn, "810")) == (["VALID"], ["VALID"])
    ingested = [e for e in orchestrator.list_audit_events(conn)
                if e["event_type"] == "DOCUMENT_INGESTED"]
    assert sorted(e["case_id"] or "" for e in ingested) == ["", out.case_id]


def test_malformed_po_quarantined_as_850_case(conn, run) -> None:
    """FR-02: a broken PO becomes its own QUARANTINED case and stops the invoice."""
    out = orchestrator.process(conn, b"{}", b"{broken", reference_dir=REFERENCE)
    c = db.get_case(conn, out.case_id)
    assert (out.outcome, c["status"], c["invoice_id"]) == ("QUARANTINED", "QUARANTINED", None)
    assert c["po_id"] is not None and doc_rows(conn, "810") == []
    assert json.loads(c["result_json"])["error"]["field"] == "<envelope>"


def test_po_replay_and_conflict(conn) -> None:
    """AT09 for the 850: identical PO is a no-op; same key, new content is a conflict."""
    po = t.po([(1, "SKU-A", "100", "EA", "2.00")])
    inv = t.invoice([(1, "SKU-A", "100", "EA", "2.00", "200.00")])
    orchestrator.process(conn, inv, po, reference_dir=REFERENCE)
    replay = orchestrator.process(conn, inv, po, reference_dir=REFERENCE)
    assert replay.outcome == "REPLAY_NOOP" and doc_rows(conn, "850") == ["VALID"]
    changed = t.po([(1, "SKU-A", "90", "EA", "2.00")])
    conflict = orchestrator.process(conn, inv, changed, reference_dir=REFERENCE)
    assert conflict.outcome == "DUPLICATE_CONFLICT"
    assert doc_rows(conn, "850") == ["VALID", "DUPLICATE_CONFLICT"]


@pytest.fixture
def three_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THREE_WAY_ENABLED", "true")


def test_receipt_branches(conn, three_way) -> None:
    """Three-way 861 intake: stored, replay ignored, malformed and unsupported profiles."""
    rcv = t.receipt([(1, "SKU-A", "100", "100", "0", "EA")])
    assert "stored" in orchestrator.ingest_receipt(conn, rcv)
    assert "replay" in orchestrator.ingest_receipt(conn, rcv)
    assert orchestrator.ingest_receipt(conn, b"{broken").startswith("861 rejected")
    no_buyer = json.loads(rcv)
    del no_buyer["buyer"]
    assert orchestrator.ingest_receipt(conn, json.dumps(no_buyer).encode()) == (
        "861 rejected: buyer — required field is missing")
    prd_no_lines = json.loads(fx("receipt_AT21.json").read_text())
    del prd_no_lines["lines"]
    assert "not usable" in orchestrator.ingest_receipt(conn, json.dumps(prd_no_lines).encode())
    assert doc_rows(conn, "861") == ["VALID", "QUARANTINED", "QUARANTINED",
                                     "UNSUPPORTED_PROFILE"]


def test_import_dataset_skips_unsupported_transactions(conn, three_way, tmp_path) -> None:
    """Dataset import: unknown transaction types are reported, never silently dropped."""
    (tmp_path / "po.json").write_bytes(t.po([(1, "SKU-A", "100", "EA", "2.00")]))
    (tmp_path / "inv.json").write_bytes(t.invoice([(1, "SKU-A", "100", "EA", "2.00",
                                                    "200.00")]))
    asn = json.loads(t.receipt([(1, "SKU-A", "100", "100", "0", "EA")]))
    asn["transaction_type"] = "856"
    (tmp_path / "asn.json").write_text(json.dumps(asn))
    summary = orchestrator.import_dataset(conn, tmp_path, reference_dir=REFERENCE)
    assert summary["counts"] == {"850": 1, "861": 0, "810": 1}
    assert summary["skipped"] == ["asn.json: unsupported transaction 856"]
    assert [r["state"] for r in summary["cases"]] == ["WAIT_RECEIPT"]
