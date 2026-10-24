# EDI Resolve — Build Plan (v2)

## Overview

Build the P0 MVP end-to-end as a working skeleton first, then fill in real logic module by module. Every screen and workflow step will be reachable before any AI integration is live.

**Stack:** Python · Streamlit · SQLite · `decimal.Decimal` arithmetic · AI provider TBD (abstracted behind stub)

**Approach:** Thin vertical slice → working skeleton → real logic per module

**Source of truth:** `docs/PRD.md`

**P0 release gate:** AT01–AT24 acceptance tests must pass (PRD Section 21–22)

> **Implementation note:** after merging PR #1, config moved to `backend/config.py` (`AI_STUB_MODE` became `AI_PROVIDER=mock|mock_unavailable|watsonx`, and `EDI_DB_PATH` became `DATABASE_PATH`). Models became the Pydantic package `backend/models/`, and the workflow service became `backend/orchestrator.py`. The task text below is kept as written.

**P2 stretch goal:** Three-way 861 receipt matching — gated behind `three_way_enabled=false` flag; NOT required for P0

---

## Architecture Overview

```
app.py (Streamlit — single process)
  ├── pages/upload.py        Upload & Process
  ├── pages/review.py        Exception Review
  └── pages/audit.py         Audit History

backend/
  ├── models.py              All dataclasses (LineItem, PO, Invoice, DiscrepancyRecord, AIExplanation, AuditEvent)
  ├── db.py                  SQLite init + all DB read/write functions (atomic audit writes)
  ├── parser.py              parse_po(), parse_invoice() — immutable, provenance-stamped
  ├── reference.py           load_uom_conversions(), load_price_contracts() — sole file I/O for data/reference/
  ├── comparator.py          compare() — deterministic rules engine only; no AI, no floats
  └── ai_resolver.py         explain() — stub by default; real provider behind AI_STUB_MODE flag

data/
  raw/                       EDI 850 PO + 810 invoice JSON fixtures
  reference/                 uom_conversions.json, price_contracts.json
  processed/                 (unused in P0)

tests/
  fixtures/                  Labeled synthetic fixture files (AT01–AT24 scenarios)
  test_parser.py
  test_comparator.py
  test_ai_resolver.py
  test_db.py
  test_acceptance.py         AT01–AT24 end-to-end acceptance tests
```

---

## Discrepancy / Case State Vocabulary

These are the only permitted result states (PRD Section 19). Do not invent new ones.

| State | Meaning |
|---|---|
| `MATCHED_2WAY` | All required P0 checks pass |
| `PRICE_VARIANCE` | Normalized unit prices differ beyond tolerance |
| `QUANTITY_VARIANCE` | Cumulative invoice quantity exceeds PO authorization |
| `PARTIAL_BILLING` | Invoice below PO balance; explicit allocation recorded |
| `INSUFFICIENT_EVIDENCE` | Missing UOM factor, price basis, currency, or contradictory refs |
| `WAIT_DOCUMENT` | PO or invoice not found |
| `REVIEW_LINK` | Ambiguous or multiple candidate line matches |
| `QUARANTINED` | Malformed envelope, unsupported profile, missing required field |
| `DUPLICATE_CONFLICT` | Same business key with different content |
| `TECHNICAL_HOLD` | Parser/rules/reference service failure |
| `NOT_EVALUATED` | Check not run (e.g., receipt check in P0, unsupported total) |
| `AI_ABSTAIN` | AI could not produce a supported explanation |
| `AI_UNAVAILABLE` | AI service timed out or returned invalid schema |

---

## Key Arithmetic Rules (PRD Section 18)

- **Decimal policy:** `decimal.Decimal`, USD, 2 decimal places, `ROUND_HALF_UP` at line extension only
- **No unit-price rounding** before computing extension
- **Zero tolerances** in prototype baseline (configurable later)
- **Normalization:** `Q_base = Q_raw × f`, `P_base = P_raw / (b × f)` where `f` = units per transaction UOM, `b` = units per price basis; require `f > 0` and `b > 0`
- **Quantity check:** `delta_quantity = Q_invoice_base - Q_PO_allocated_base` (partial invoice ≠ shortage)
- **Extension check:** `expected_ext = round_money(Q_invoice_base × P_PO_base)`; validate invoice-provided extensions independently

---

## Sub-Tasks

---

### Task 1 — Project Foundation

**Status:** `[x] done`

**Intent:** Establish dependencies, secrets template, SQLite schema, and a runnable `app.py` stub so every subsequent task has a stable base.

**Expected Outcomes:**
- `requirements.txt` populated with all core dependencies
- `.env.example` documents every required environment variable
- `backend/db.py` initializes all SQLite tables idempotently with `CREATE TABLE IF NOT EXISTS`
- `app.py` runs with `streamlit run app.py` (sidebar nav stub, calls `init_db()` on startup)

**Todo List:**
1. Populate `requirements.txt`: `streamlit`, `python-dotenv`, `pytest`, `ruff`; add comment `# AI SDK TBD: ibm-watsonx-ai or openai`
2. Create `.env.example` with: `AI_STUB_MODE=true`, `AI_API_KEY=`, `AI_API_URL=`, `AI_MODEL_ID=`, `THREE_WAY_ENABLED=false`
3. Create `backend/db.py` with `init_db()` — creates these tables using `CREATE TABLE IF NOT EXISTS`:
   - **`ingested_documents`**: `id`, `doc_type` (850/810/861), `original_bytes_path`, `content_hash`, `ingested_at`, `tenant`, `trading_partner`, `parser_version`, `validation_status`, `validation_errors`
   - **`reconciliation_cases`**: `id`, `po_id`, `invoice_id`, `status`, `mode` (2way/3way), `created_at`, `updated_at`, `evidence_version`
   - **`case_findings`**: `id`, `case_id`, `finding_type` (from state vocabulary), `po_value`, `invoice_value`, `normalized_po_value`, `normalized_invoice_value`, `delta`, `evidence_json`, `created_at`
   - **`audit_events`**: `id`, `case_id`, `event_type`, `actor`, `event_at`, `before_state`, `after_state`, `doc_hashes`, `reference_versions`, `calculations_json`, `recommendation_version`, `reviewer_reason`, `escalation_owner`, `idempotency_key`
4. All audit writes in `db.py` must be wrapped in a transaction — if the write fails, the caller receives an error and no state change is committed (PRD Section 20.2: atomic audit persistence)
5. Create `app.py` — calls `init_db()` at top, renders sidebar with page links: "Upload & Process", "Exception Review", "Audit History"
6. Create `pages/` directory with empty `upload.py`, `review.py`, `audit.py` stubs

**Relevant Context:**
- `backend/__init__.py` is currently empty
- SQLite `.db` is gitignored — schema must be fully recreatable
- `THREE_WAY_ENABLED=false` disables P2 receipt path; do not implement 861 semantic parsing in P0

---

### Task 2 — Synthetic Data Fixtures (AT01–AT24 Coverage)

**Status:** `[x] done`

**Intent:** Create labeled synthetic EDI fixture files covering the full P0 acceptance test suite (AT01–AT24). These are the test inputs for Task 6 acceptance testing. Data Science will enrich later.

**Expected Outcomes:**
- At least 10 PO/invoice pairs in `data/raw/` (PRD Section 8 minimum)
- Fixtures span: valid match, price mismatch, UOM mismatch, insufficient evidence, partial billing, quantity variance, duplicate/conflict, malformed, missing PO, currency mismatch
- `data/reference/uom_conversions.json` and `data/reference/price_contracts.json` populated
- Each fixture is labeled with its expected AT result state

**Todo List:**
1. Define canonical EDI 850 JSON schema (all prices as `str`):
   ```json
   {
     "doc_type": "850", "po_number": "str", "po_date": "YYYY-MM-DD",
     "buyer": "str", "supplier": "str", "currency": "USD",
     "lines": [{
       "line_id": "str", "product_id": "str", "upc": "str",
       "description": "str", "quantity": "str", "uom": "str",
       "unit_price": "str", "price_basis_qty": "str"
     }]
   }
   ```
2. Define canonical EDI 810 JSON schema (all prices as `str`):
   ```json
   {
     "doc_type": "810", "invoice_number": "str", "invoice_date": "YYYY-MM-DD",
     "po_number": "str", "buyer": "str", "supplier": "str", "currency": "USD",
     "lines": [{
       "line_id": "str", "product_id": "str", "upc": "str",
       "quantity": "str", "uom": "str", "unit_price": "str",
       "price_basis_qty": "str", "extended_price": "str"
     }]
   }
   ```
3. Create fixtures for these AT scenarios (each file labeled with expected result in a `_expected_result` field):
   - `po_AT01.json` / `inv_AT01.json` — 100 EA at $2.00 → MATCHED_2WAY
   - `po_AT02.json` / `inv_AT02.json` — 50 CS at $24/CS vs 600 EA at $2/EA, 12 EA/CS conversion → MATCHED_2WAY
   - `po_AT03.json` / `inv_AT03.json` — same as AT02 but no conversion record → INSUFFICIENT_EVIDENCE
   - `po_AT04.json` / `inv_AT04.json` — 100 EA at $2.00 vs $2.10 → PRICE_VARIANCE
   - `po_AT05.json` / `inv_AT05.json` — AT04 + contract at $2.10 effective on order date → PRICE_VARIANCE (contract displayed separately, not a match)
   - `po_AT07.json` / `inv_AT07.json` — PO 100 EA, invoice 40 EA explicit allocation → PARTIAL_BILLING
   - `po_AT08a.json` / `inv_AT08a.json` — PO 100 EA, prior invoice allocated 70 EA (fixture)
   - `po_AT08b.json` / `inv_AT08b.json` — new invoice 40 EA → QUANTITY_VARIANCE (cumulative 110)
   - `po_AT09.json` / `inv_AT09.json` — identical invoice for duplicate test → REPLAY_NOOP on second ingest
   - `inv_AT09_conflict.json` — same invoice ID, different quantity → DUPLICATE_CONFLICT
   - `inv_AT11_malformed.json` — missing required quantity field → QUARANTINED
   - `po_AT12.json` / `inv_AT12_currency.json` — currency mismatch (EUR invoice vs USD PO) → held
4. Create `data/reference/uom_conversions.json`:
   ```json
   { "PROD-CS-001": { "case_to_each": "12", "effective_date": "2026-01-01" } }
   ```
5. Create `data/reference/price_contracts.json`:
   ```json
   { "PROD-CS-001": [{ "unit_price": "2.10", "uom": "each", "effective_date": "2026-01-01", "expiry_date": "2026-12-31" }] }
   ```
6. Create `tests/fixtures/` directory and move all AT fixture files there; add a `fixtures_manifest.json` listing each fixture and its expected result

**Relevant Context:**
- `price_basis_qty` is the `b` value in the normalization formula — must be stored, not assumed to be 1
- Prices are strings throughout fixtures; never `float` or `number` JSON type
- AT06 (AI abstains with no contract) and AT10 (missing PO, repeated SKU) will be exercised at runtime, not as file fixtures
- AT13 (decimal precision: $0.335 × 3) and AT14 (tolerance boundary) are created as small targeted unit fixtures

---

### Task 3 — Parser Module + Ingestion Provenance

**Status:** `[x] done`

**Intent:** Build `backend/parser.py` to load, validate, and stamp EDI JSON fixtures with provenance (content hash, ingestion time, parser version). Store immutable source fields alongside normalized values as required by PRD Section 17.1.

**Expected Outcomes:**
- `parse_po(path) -> PurchaseOrder` and `parse_invoice(path) -> Invoice` return typed dataclasses
- All price/quantity fields are `decimal.Decimal` after parsing; original string values retained in a `raw_fields` dict
- Missing required fields raise `ParseError` with exact source field name
- Content hash (SHA-256 of file bytes) is computed and stored on the parsed object
- Duplicate content hash detection is possible at ingestion time
- Unit tests pass: valid load, missing field, decimal precision (AT13: $0.335 × 3 = $1.01), currency capture

**Todo List:**
1. Create `backend/models.py` — define dataclasses:
   - `RawLineItem`: all fields as `str` (exact source values)
   - `LineItem`: typed fields (`decimal.Decimal` for price/qty, `str` for IDs), plus `raw: RawLineItem`
   - `PurchaseOrder`: header fields + `lines: list[LineItem]` + `content_hash: str` + `ingested_at: str`
   - `Invoice`: same pattern as PurchaseOrder
   - `DiscrepancyRecord`, `AIExplanation`, `AuditEvent` (stubs — filled in later tasks)
2. Create `backend/parser.py`:
   - `parse_po(path: str) -> PurchaseOrder`
   - `parse_invoice(path: str) -> Invoice`
   - Compute SHA-256 of file bytes before parsing; store as `content_hash`
   - Validate: required fields present, numeric fields parse to valid `Decimal`, UOM non-empty, currency present
   - On any validation failure raise `ParseError(field_name, raw_value, message)` — never silently default
   - Retain original string values in `raw` fields alongside `Decimal` casts
3. Create `backend/exceptions.py` — define `ParseError`, `ReconciliationError`, `AuditPersistenceError`
4. Create `tests/test_parser.py`:
   - Valid load of AT01 fixture → correct `Decimal` values
   - Missing `quantity` field → `ParseError` naming the field
   - AT13 precision: `"0.335"` → `Decimal("0.335")` (no float rounding drift)
   - Content hash is stable across repeated loads of same file
   - Currency field captured on both PO and invoice

**Relevant Context:**
- PRD 17.1: "retain original units, currency, precision, identifiers and dates alongside normalized values"
- PRD 17.1: "no guessed default currency, UOM, price basis, receipt status, or product mapping"
- `hashlib.sha256` is stdlib — no extra dependency

---

### Task 4 — Reference Data Loader

**Status:** `[x] done`

**Intent:** Build `backend/reference.py` as the sole I/O owner for `data/reference/`. All comparator logic reads reference data through this module, never directly from files.

**Expected Outcomes:**
- `load_uom_conversions() -> dict` and `load_price_contracts() -> dict` load and validate reference files
- Conversion factors and prices are `decimal.Decimal`
- Missing, expired, or overlapping contract records are detected and surfaced (not silently ignored)
- Unit tests cover: valid load, missing file, expired contract, overlapping contract dates

**Todo List:**
1. Create `backend/reference.py`:
   - `load_uom_conversions(path: str = "data/reference/uom_conversions.json") -> dict[str, UOMConversion]`
   - `load_price_contracts(path: str = "data/reference/price_contracts.json") -> dict[str, list[PriceContract]]`
   - `get_conversion(product_id: str, uom_from: str, uom_to: str, conversions: dict) -> Decimal | None` — returns `None` if missing (caller handles `INSUFFICIENT_EVIDENCE`)
   - `get_contract_price(product_id: str, effective_date: str, contracts: dict) -> PriceContract | None` — returns `None` if no valid contract on that date
2. Add `UOMConversion` and `PriceContract` dataclasses to `backend/models.py`
3. Create `tests/test_reference.py` — test valid load, missing file returns graceful result, expired contract returns `None`, overlapping dates raises `ReconciliationError`

**Relevant Context:**
- PRD 17.3: "missing, expired, overlapping, or contradictory records yield INSUFFICIENT_EVIDENCE"
- PRD 17.3: "external catalogs cannot establish pack size, agreed price, or identity for financial decisions"
- Comparator must never open `data/reference/` files directly — all access through `reference.py`

---

### Task 5 — Comparator / Rules Engine

**Status:** `[x] done`

**Intent:** Build `backend/comparator.py` — the deterministic rules engine implementing the full PRD Section 18 normalization formula and Section 19 decision table for P0 two-way reconciliation.

**Expected Outcomes:**
- `compare(po, invoice, uom_ref, price_ref) -> list[DiscrepancyRecord]` handles all P0 result states
- Normalization formula (`Q_base = Q_raw × f`, `P_base = P_raw / (b × f)`) implemented correctly
- Partial billing distinguished from quantity variance (PRD 18.2)
- Cumulative invoice allocation tracked against PO (AT08 scenario)
- Currency mismatch held with `NOT_EVALUATED`, not silently compared
- All arithmetic uses `decimal.Decimal` with `ROUND_HALF_UP` at line extension only
- Tests cover AT01–AT08, AT12, AT13, AT14

**Todo List:**
1. Finalize `DiscrepancyRecord` in `backend/models.py`:
   - `case_id`, `po_line`, `invoice_line`, `result_state` (from vocabulary), `po_value_raw`, `invoice_value_raw`, `po_value_normalized`, `invoice_value_normalized`, `delta`, `tolerance_applied`, `evidence: list[str]`, `not_evaluated_checks: list[str]`
2. Create `backend/comparator.py`:
   - Line matching: match by `product_id` first, then `upc`; ambiguous/unmatched → `REVIEW_LINK` or `WAIT_DOCUMENT`
   - Currency check first: if `po.currency != invoice.currency` → hold with `NOT_EVALUATED` extension check
   - UOM normalization: compute `f` and `b` from reference; if `f` or `b` missing → `INSUFFICIENT_EVIDENCE` for price and quantity checks (independent checks may still run)
   - Price comparison: `delta_price = P_invoice_base - P_PO_base`; apply zero tolerance by default; flag `PRICE_VARIANCE` if `abs(delta_price) > tolerance`
   - Quantity check: check cumulative allocated quantity against PO authorization; partial invoice → `PARTIAL_BILLING`; cumulative overage → `QUANTITY_VARIANCE`
   - Extension validation: compute `expected_ext = round_money(Q_invoice_base × P_PO_base)`; if invoice `extended_price` differs, record finding independently
   - Contract price displayed as separate evidence field — never overwrites PO price
   - `CHECK_NOT_EVALUATED` stays distinct from `CHECK_FAILED` (PRD Section 19)
3. Create `backend/allocation.py` — `get_allocated_quantity(po_id, line_id, db_conn) -> Decimal` queries existing approved allocations from DB; used for AT08 cumulative check
4. Create `tests/test_comparator.py` — test each AT scenario from AT01–AT08, AT12, AT13 ($0.335 precision), AT14 (tolerance boundary $0.01 vs $0.0101)

**Relevant Context:**
- PRD 18.1: "pin the profile, reference snapshot, applicable date, precision, rounding, tolerances and mode for every run"
- PRD 18.2: "do not close an entire PO based on one invoice"
- PRD 19: "blocking validation or ambiguous evidence prevents a verified pass; AI does not resolve a blocking condition"
- PRD 19: "CHECK_NOT_EVALUATED must remain distinct from CHECK_FAILED"

---

### Task 6 — AI Explanation Module

**Status:** `[x] done`

**Intent:** Build `backend/ai_resolver.py` with a clean interface returning structured AI output. Start with a stub; real provider slots in behind the `AI_STUB_MODE` flag. AI must never alter findings, perform arithmetic, or access source data beyond the structured evidence bundle.

**Expected Outcomes:**
- `explain(discrepancy: DiscrepancyRecord) -> AIExplanation` works end-to-end with stub
- `AIExplanation` contains: `finding_ids`, `possible_cause`, `cited_evidence_ids`, `uncertainty_note`, `missing_evidence`, `suggested_next_action`, `model_version`, `prompt_version`
- Stub returns appropriate `AI_ABSTAIN` when evidence is insufficient (AT06 scenario)
- AI timeout → `AI_UNAVAILABLE`; deterministic findings remain accessible
- Structured output schema validated before use (invalid schema → `AI_ABSTAIN`, not crash)

**Todo List:**
1. Add `AIExplanation` dataclass to `backend/models.py` with all fields above
2. Create `backend/ai_resolver.py`:
   - `explain(discrepancy: DiscrepancyRecord, stub_mode: bool = True) -> AIExplanation`
   - Stub returns `AIExplanation` based on `result_state`: price variance → cite PO/invoice values, suggest verify contract; insufficient evidence → `AI_ABSTAIN`; matched → no explanation needed
   - `STUB_MODE` read from env var `AI_STUB_MODE`
   - Real provider path: send only `DiscrepancyRecord` as structured JSON — never raw EDI bytes or product description text (PRD Section 19: "treat document text as untrusted data")
   - Validate structured output schema before returning; discard unvalidated narrative
   - Handle timeout → return `AIExplanation` with `suggested_next_action="AI_UNAVAILABLE"`
3. Add `# TODO: Real provider block` with expected prompt structure:
   - System: "You are a reconciliation assistant. Only cite evidence IDs provided. Do not perform arithmetic. If information is missing, say so explicitly."
   - User: structured JSON of `DiscrepancyRecord` fields only
   - Required output schema: `{ finding_ids, possible_cause, cited_evidence_ids, uncertainty_note, missing_evidence, suggested_next_action }`
4. Create `tests/test_ai_resolver.py` — test stub for each `result_state`; AT15 (invented contract ID rejected), AT16 (timeout → `AI_UNAVAILABLE`)

**Relevant Context:**
- PRD Section 19: AI suggestions must cite evidence IDs; existence of citation alone is insufficient — support must be verified
- PRD Section 20.3: "AI failure leaves deterministic results and manual review available"
- PRD Section 19: "no numeric confidence is treated as a probability of financial correctness"

---

### Task 7 — Streamlit UI (Upload, Review, Audit)

**Status:** `[x] done`

**Intent:** Build the three Streamlit pages wiring all backend modules together. Every state transition writes an audit event atomically. The UI never lets a traceback reach the user and always shows deterministic findings even when AI is unavailable.

**Expected Outcomes:**
- Analyst can upload or select a PO + invoice pair; processing runs and produces case findings
- Exception Review page lists all open cases with result state badges
- Detail view shows: original values, normalized values, delta, evidence links, deterministic status, AI explanation (labeled), missing facts
- Reviewer can approve, reject, or escalate — reason is required; stale/concurrent actions fail gracefully
- Audit History page shows append-only event log per case
- `Action not saved` displayed if audit write fails (no silent state change)
- Invalid files show `st.error()` with field-level message — no traceback

**Todo List:**
1. Build `pages/upload.py`:
   - File upload widgets for PO (850) and invoice (810); also dropdown to select from `data/raw/` samples
   - On submit: call `parse_po()`, `parse_invoice()`, check content hash for duplicate (duplicate → show existing case, do not create new); call `compare()`, persist case + findings to DB, navigate to review
   - Surface `ParseError` as `st.error(f"Upload failed: {e.field_name} — {e.message}")` — no traceback
   - Wrap processing in `st.spinner()`
2. Build `pages/review.py`:
   - Query all cases from DB; render as table with `result_state` badge and status (open/approved/rejected/escalated)
   - Expandable detail row: original fields, normalized fields, delta, evidence cited, `NOT_EVALUATED` checks listed separately
   - AI explanation card: show `possible_cause`, `cited_evidence_ids`, `uncertainty_note`; label clearly as "AI Suggestion — requires human review"; if `AI_UNAVAILABLE` show deterministic findings only
   - Review form: radio (Approve / Reject / Escalate), required reason text area, escalation owner field if Escalate selected
   - On submit: write audit event + update case atomically; if `AuditPersistenceError` → show "Action not saved. Please retry." — do not update case status
   - Optimistic version check: if case version changed since page load, show "Case was updated by another action. Please refresh."
3. Build `pages/audit.py`:
   - Query `audit_events` by case; render append-only history table: event type, actor, timestamp, before/after state, reviewer reason, escalation owner
   - Allow filtering by case ID
4. Handle `THREE_WAY_ENABLED=false` throughout: if 861 is uploaded, store it with an "unsupported for reconciliation" message; display receipt check as `NOT_EVALUATED`

**Relevant Context:**
- PRD 20.1: "reviewers must be able to inspect the source without accepting the AI suggestion"
- PRD 20.2: "persist an action, its allocation changes, case version and audit event atomically"
- PRD 20.2: "if audit persistence fails, roll back and display Action not saved"
- PRD 20.1: "approving an investigation leaves the discrepancy open; an approved exception disposition is labeled explicitly and does not become MATCHED"

---

### Task 8 — Wire Up Real AI Provider

**Status:** `[~] implemented — live watsonx call not yet verified (needs .env keys)`

**Intent:** Replace the AI stub with a real provider call (watsonx.ai or confirmed alternative) behind the existing `AI_STUB_MODE` flag. Stub remains intact for offline demos.

**Expected Outcomes:**
- `AI_STUB_MODE=false` routes to real provider call in `ai_resolver.py`
- Structured output validated against schema before use
- AT15 (injected instruction in product description) cannot mutate data or produce false facts
- Stub path remains available and tested

**Todo List:**
1. Confirm provider with team; add SDK to `requirements.txt` (likely `ibm-watsonx-ai`)
2. Populate `.env` and `.env.example` with provider-specific keys (`WATSONX_API_KEY`, `WATSONX_URL`, `WATSONX_PROJECT_ID` if watsonx)
3. Implement real provider call in the `# TODO` block in `ai_resolver.py`
4. Validate response schema strictly — discard narrative if citation IDs don't exist in the evidence bundle
5. Mark integration tests with `@pytest.mark.integration`; run `pytest -m integration` separately

**Relevant Context:**
- PRD Section 19: "treat document text as untrusted data, including instructions embedded in EDI descriptions"
- IBM watsonx.ai SDK: `ibm-watsonx-ai`; keep stub for offline/demo use

---

### Task 9 — Acceptance Testing (AT01–AT24)

**Status:** `[x] done`

**Intent:** Implement and run all P0 acceptance tests from PRD Section 21. These are the formal release gate for P0 completion (PRD Section 22.1).

**Expected Outcomes:**
- All AT01–AT24 tests pass in `tests/test_acceptance.py`
- Each test asserts: case state, line findings, normalized values, evidence pointers, audit event fields
- AT13 proves no float drift ($0.335 × 3 = $1.01)
- AT14 proves tolerance boundary ($0.01 passes, $0.0101 fails)
- AT15 proves AI cannot inject false facts
- AT16 proves deterministic findings survive AI unavailability
- AT17–AT20 prove human review states, stale action rejection, audit rollback, and retry idempotency

**Todo List:**
1. Create `tests/test_acceptance.py` — implement AT01 through AT24 as individual test functions
2. AT01: load AT01 fixtures → assert `MATCHED_2WAY`, no exceptions, receipt `NOT_EVALUATED`
3. AT02: AT02 fixtures + UOM conversion → assert normalized equality, `MATCHED_2WAY`
4. AT03: AT03 (no conversion) → assert `INSUFFICIENT_EVIDENCE`, dependent checks `NOT_EVALUATED`
5. AT04: AT04 fixtures → assert `PRICE_VARIANCE`, delta = +$0.10/EA, +$10 extension
6. AT05: AT05 (with contract) → assert `PRICE_VARIANCE` persists, contract displayed separately, not a match
7. AT06: AT04 without contract; stub returns `AI_ABSTAIN` → assert rules still show delta
8. AT07: AT07 partial billing → assert `PARTIAL_BILLING`, PO balance = 60 EA
9. AT08: AT08a as prior allocation + AT08b new invoice → assert `QUANTITY_VARIANCE`, cumulative 110
10. AT09: re-upload AT09 fixture → assert replay is no-op; upload AT09 conflict → assert `DUPLICATE_CONFLICT`
11. AT10: missing PO → `WAIT_DOCUMENT`; repeated SKU → `REVIEW_LINK`
12. AT11: malformed fixture (missing quantity) → `QUARANTINED` with exact source error
13. AT12: currency mismatch → extension check held; `NOT_EVALUATED` not a pass
14. AT13: $0.335 × 3 → extension $1.01, not $1.005 or $1.00 (proves `ROUND_HALF_UP` at extension)
15. AT14: configure $0.01 tolerance; delta $0.01 passes, $0.0101 fails; both persist actual delta + policy version
16. AT15: AI receives fixture with injected instruction in description → assert no data mutation, `AI_ABSTAIN` or rules-only
17. AT16: mock AI timeout → assert `AI_UNAVAILABLE`, deterministic findings still shown
18. AT17: three review actions (approve, reject, escalate) → assert three distinct states + reasons saved
19. AT18: approval attempt with missing UOM evidence → assert blocked; submission without reason → assert blocked; stale version → assert refused
20. AT19: mock audit write failure during approval → assert state rolled back, "Action not saved" shown, retry records one commit
21. AT20: mock rules engine failure → assert `TECHNICAL_HOLD`, bounded retry, no false pass
22. AT21: upload 861 with `THREE_WAY_ENABLED=false` → assert `NOT_EVALUATED`, never `MATCHED_3WAY`
23. AT22: correct source evidence after AI recommendation → assert new rerun, stale suggestion unapprovable, history retrievable
24. AT24: missing price basis → dependent check blocked; independent totals variance retained

**Relevant Context:**
- PRD Section 22.1: "P0 completion requires AT01–AT24 on supported synthetic fixtures; record actual outcomes"
- AT23 (cross-tenant isolation) is relevant only if multi-tenancy is implemented; mark as skipped in single-tenant prototype
- AT25–AT36 are P2 only — do not implement unless `THREE_WAY_ENABLED=true` is set and approved

---

### Task 10 — Demo Readiness and Documentation

**Status:** `[x] done`

**Intent:** Ensure the full P0 workflow is demonstrable end-to-end, all metrics are measurable, and known limitations are documented. Move PRD to `docs/` and update `README.md`.

**Expected Outcomes:**
- `pytest` (unit + acceptance) passes with no failures
- Full user journey walkable for AT01, AT04, AT07, AT06 (insufficient evidence) scenarios in the UI
- `README.md` has setup, `.env` config, run instructions, supported scenarios, known limitations
- Definition of Done (PRD Section 13) met item by item

**Todo List:**
1. Run `pytest` and fix any failures
2. Grep for `float(` in `backend/` — replace any occurrences with `Decimal`
3. Walk the full UI journey manually for AT01, AT04, AT07, AT06 scenarios
4. Verify audit history retrieval works for all reviewed cases
5. Update `README.md`: setup steps, `.env` config, `streamlit run app.py`, supported scenarios list, known limitations
6. Create `docs/` directory; move `Resources/SpartHill Group_15 PRD (2).md` to `docs/PRD.md`; update all plan/AGENTS references to new path
7. Confirm Definition of Done per PRD Section 13 item by item and document actual test results in `docs/test_results.md`
8. Record actual per-category detection accuracy (PRD Section 22.2: "report per-category outcomes so a majority of easy matches cannot hide misses")

**Relevant Context:**
- PRD Section 13 DoD: PO+invoice load, correct detection, evidence-backed explanation, decision recording, audit history, one insufficient-evidence case, documented limitations, reliable demo
- PRD Section 22.2: report metrics separately; no production payment-timing claim from synthetic results

---

## Key Constraints (apply to all tasks)

- All price and quantity arithmetic uses `decimal.Decimal` — never `float`
- `ROUND_HALF_UP` applied at line extension only — no unit-price rounding before extension
- AI module never performs or verifies financial calculations; never receives raw EDI text
- `CHECK_NOT_EVALUATED` ≠ `CHECK_FAILED` — keep these states distinct everywhere
- Every audit write is atomic — if it fails, the action is not acknowledged and state is not changed
- Reviewer reason is required for every approve/reject/escalate action
- `THREE_WAY_ENABLED=false` by default — no 861 semantic parsing in P0
- `AI_STUB_MODE=true` by default — real provider call is an opt-in
- No approval action posts, pays, transmits a correction, or mutates an external ledger
- `init_db()` is idempotent — safe to call on every app startup
- No secrets hardcoded anywhere
