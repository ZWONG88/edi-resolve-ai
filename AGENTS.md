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
- **AI provider**: IBM watsonx.ai (Granite) via `ibm-watsonx-ai`, behind `backend/ai_resolver.py`; offline mock is the default (`AI_PROVIDER=mock`)

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
  reference/  # uom_conversions.json, price_contracts.json,
              #   master_catalog.json (synthetic catalogue — display enrichment only)
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
  config.py       # Settings (pydantic-settings) — every env var; get_settings()
  db.py           # init_db() — SQLite schema (atomic audit writes); call at app startup
  models/         # Pydantic v2 contract (from PR #1): common, documents, reference,
                  #   reconciliation, review, ai — import via `from backend.models import ...`
  exceptions.py   # ParseError, ReconciliationError, AuditPersistenceError
  parser.py       # parse_po(), parse_invoice() → Pydantic models; "sha256:" hash + raw strings
  adapters.py     # team-format JSON (transaction_type, arrays) → models; exact Decimals
  reference.py    # sole I/O for data/reference/; comparator must not open files directly;
                  #   load_master_catalog() for the synthetic catalogue (UI enrichment only)
  comparator.py   # compare() — deterministic rules only; no AI, no floats
  allocation.py   # cumulative PO (AT08) and accepted-receipt (AT31) allocations
  receipts.py     # 861 ledger: incremental/cumulative, replay, ambiguity
  disposition.py  # dollar impact per line + invoice disposition (rules doc 3-5)
  money.py        # round_money() — ROUND_HALF_UP at extension only
  remittance.py   # EDI 820 payload (generated, never transmitted)
  ai_resolver.py  # explain() → AIExplanation; AI_PROVIDER=mock | mock_unavailable | watsonx
  orchestrator.py # the ONLY backend module pages import: process(), import_dataset(),
                  #   ingest_receipt(), rerun_case(),
                  #   request_ai_explanation(), review_action(ReviewRequest), list_*()
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
- **`AI_PROVIDER=mock`** default — real provider (`watsonx`) is opt-in; all config goes through `backend/config.py`; **`THREE_WAY_ENABLED=true`** default — the team rules doc makes 850+810+861 matching the core flow; `false` falls back to two-way (no auto-approval, no 820)
- **Team rules doc** (`docs/rules-comparison.md`) is followed: auto-approve clean three-way matches, short-pay discrepancies, generate EDI 820 files — **never transmit** them; bill on **accepted** (not received) quantity
- **Master catalogue** (`data/reference/master_catalog.json`) is synthetic and **enrichment only** (PRD §17.3): it contains random price picks and inferred pack sizes, so it is never a source of price, pack size, UOM factor or identity for decisions. It is shown via `orchestrator.catalog_enrichment()`, never passed to the comparator, and kept out of `reference_version()`
- Team-format datasets (`transaction_type` JSON) are read only through `backend/adapters.py`; dollar impact/disposition only in `backend/disposition.py`
- **Release gate**: AT01–AT24 (`tests/test_acceptance.py`), AT25–AT35 except AT33 (`tests/test_three_way.py`) and the frozen team dataset outcomes (`tests/test_datasets.py`) must all pass

## Code Style

- PEP 8; type hints on all function signatures
- Linter: `ruff` (config in `pyproject.toml`)
- Unit prices are `Money(amount=...)` at full precision — never `Money.from_str()` (it rounds to cents)
- `price_basis` is `None` when the source omits it — never default it to 1
