"""Shared pytest fixtures: isolated temp database and fixture paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend import db, service

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = FIXTURES / "reference"


def fx(name: str) -> Path:
    return FIXTURES / name


@pytest.fixture(autouse=True)
def _stub_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_STUB_MODE", "true")
    monkeypatch.setenv("THREE_WAY_ENABLED", "false")


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_file: Path) -> sqlite3.Connection:
    connection = db.init_db(db_file)
    yield connection
    connection.close()


@pytest.fixture
def run(conn: sqlite3.Connection):
    """Process an invoice (and optional PO) against the test reference set."""

    def _run(invoice: str, po: str | None = None, **kwargs) -> service.IngestOutcome:
        return service.process(
            conn, fx(invoice), fx(po) if po else None, reference_dir=REFERENCE, **kwargs
        )

    return _run
