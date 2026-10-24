"""Shared pytest fixtures: isolated temp database and fixture paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend import db, orchestrator

FIXTURES = Path(__file__).parent / "fixtures"
REFERENCE = FIXTURES / "reference"
DATASETS = FIXTURES / "datasets"


def fx(name: str) -> Path:
    return FIXTURES / name


@pytest.fixture(autouse=True)
def _isolated_settings(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Env vars beat a developer's .env, so tests always run offline and isolated."""
    if request.node.name.startswith(("test_settings", "test_three_way")):
        return  # PR #1 settings tests isolate themselves with _env_file
    monkeypatch.setenv("AI_PROVIDER", "mock")
    monkeypatch.setenv("THREE_WAY_ENABLED", "false")
    monkeypatch.setenv("MAX_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "default.db"))
    monkeypatch.setenv("REMITTANCE_DIR", str(tmp_path / "820"))


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

    def _run(invoice: str, po: str | None = None, **kwargs) -> orchestrator.IngestOutcome:
        return orchestrator.process(
            conn, fx(invoice), fx(po) if po else None, reference_dir=REFERENCE, **kwargs
        )

    return _run
