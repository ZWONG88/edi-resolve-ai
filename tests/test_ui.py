"""Headless smoke tests for the Streamlit pages."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

import ui_shared


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ui.db"))
    ui_shared.get_conn.clear()
    yield
    ui_shared.get_conn.clear()


def test_upload_review_audit_flow() -> None:
    up = AppTest.from_file("../pages/upload.py", default_timeout=30).run()
    up.selectbox[0].set_value("AT04 · Price variance $2.00 vs $2.10").run()
    up.button[0].click().run()
    assert not up.exception
    assert any("PRICE_VARIANCE" in m.value for m in up.markdown)

    review = AppTest.from_file("../pages/review.py", default_timeout=30).run()
    assert not review.exception
    next(b for b in review.button if b.label == "Get AI suggestion").click().run()
    assert any("AI Suggestion — requires human review" in m.value for m in review.markdown)
    next(b for b in review.button if "Submit" in str(b.label)).click().run()
    assert any("reason must be a non-empty string" in e.value for e in review.error)

    audit = AppTest.from_file("../pages/audit.py", default_timeout=30).run()
    assert not audit.exception and len(audit.dataframe) == 1


def test_quarantine_shows_field_error() -> None:
    up = AppTest.from_file("../pages/upload.py", default_timeout=30).run()
    up.selectbox[0].set_value("AT11 · Malformed invoice (missing quantity)").run()
    up.button[0].click().run()
    assert any("lines[0].quantity" in e.value for e in up.error)
