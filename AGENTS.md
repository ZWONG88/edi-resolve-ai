# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Project

**EDIResolve AI** — AI-powered EDI transaction discrepancy detection and resolution (SpartHill Hacks 2026 IBM AI Challenge). Python backend with a Streamlit dashboard.

**PRD:** `docs/PRD.md` — source of truth for scope and acceptance criteria
**Build Plan:** `edi-resolve-plan.md` (project root) — 10-task implementation plan; P0 release gate = AT01–AT24 pass

## Stack

- **Language**: Python (virtual env at `.venv/`)
- **UI**: Streamlit (`streamlit run app.py`)
- **Database**: SQLite (`.db` files, gitignored)
- **Config/Secrets**: `.env` (backend logic via `python-dotenv`); `.streamlit/secrets.toml` (Streamlit UI layer) — never commit either
- **AI provider**: IBM watsonx.ai (Granite) via `ibm-watsonx-ai`, behind `backend/ai_resolver.py`; offline stub is the default (`AI_STUB_MODE=true`)

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run app
streamlit run app.py

# Run all tests
pytest

# Run a single test
pytest tests/test_parser.py::test_price_precision

# Lint
ruff check .
```

## Data Layout

```
data/
  raw/        # EDI 850 PO + 810 invoice JSON files (po_001.json, inv_001.json, ...)
  processed/  # parsed/structured output (unused for now)
  reference/  # uom_conversions.json, price_contracts.json
```

## Key Domain Concepts

- **EDI 850**: Purchase orders from retailers
- **EDI 810**: Invoices from suppliers
- Result states (complete list in plan): `MATCHED_2WAY` · `PRICE_VARIANCE` · `QUANTITY_VARIANCE` · `PARTIAL_BILLING` · `INSUFFICIENT_EVIDENCE` · `WAIT_DOCUMENT` · `REVIEW_LINK` · `QUARANTINED` · `DUPLICATE_CONFLICT` · `TECHNICAL_HOLD` · `NOT_EVALUATED` · `AI_ABSTAIN` · `AI_UNAVAILABLE`
- `CHECK_NOT_EVALUATED` ≠ `CHECK_FAILED` — never conflate these
- All price/quantity fields stored as `str` in JSON fixtures, cast to `decimal.Decimal` in `backend/parser.py` — never `float`
- `ROUND_HALF_UP` applied at line extension only — not at unit price

## Module Map

```
backend/
  __init__.py
  db.py           # init_db() — SQLite schema (atomic audit writes); call at app startup
  models.py       # all dataclasses: LineItem, PO, Invoice, DiscrepancyRecord, AIExplanation, AuditEvent
  exceptions.py   # ParseError, ReconciliationError, AuditPersistenceError
  parser.py       # parse_po(path), parse_invoice(path) — SHA-256 hash + raw field retention
  reference.py    # sole I/O for data/reference/; comparator must not open files directly
  comparator.py   # compare() — deterministic rules only; no AI, no floats
  allocation.py   # get_allocated_quantity() — cumulative PO allocation check (AT08)
  ai_resolver.py  # explain() → AIExplanation; AI_STUB_MODE=true by default
  service.py      # workflow: process(), rerun_case(), request_ai_explanation(), review_action()
app.py            # Streamlit entry; calls init_db() before any page renders
ui_shared.py      # Streamlit helpers: cached DB connection, demo scenarios, mode banner
pages/
  upload.py       # Upload & Process
  review.py       # Exception Review (atomic audit write on submit)
  audit.py        # Audit History (append-only)
tests/
  fixtures/       # AT01–AT24 labeled synthetic fixture JSON files
  test_parser.py
  test_reference.py
  test_comparator.py
  test_ai_resolver.py
  test_db.py
  test_ui.py          # headless Streamlit AppTest smoke tests
  test_acceptance.py  # AT01–AT24 formal release gate tests
```

## Critical Rules

- **Financial arithmetic**: `decimal.Decimal` everywhere; `ROUND_HALF_UP` at line extension only
- **AI must not perform arithmetic** and must never receive raw EDI text (prompt injection risk)
- **AI output must be labeled** in the UI as "AI Suggestion — requires human review"
- **Reviewer reason required** before any approve/reject/escalate; stale/concurrent actions must fail gracefully
- **Atomic audit writes**: if `AuditPersistenceError`, roll back state and display "Action not saved" — never silently succeed
- **`init_db()`** is idempotent — safe to call on every app startup
- **`AI_STUB_MODE=true`** default — real provider is opt-in; **`THREE_WAY_ENABLED=false`** default — P2 receipt matching is opt-in
- **P0 release gate**: AT01–AT24 in `tests/test_acceptance.py` must all pass

## Code Style

- PEP 8; type hints on all function signatures
- Linter: `ruff` — add to `requirements.txt`
