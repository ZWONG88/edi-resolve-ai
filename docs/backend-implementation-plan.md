# EDIResolve AI — Backend Implementation Plan

**Owner:** Backend Engineer
**Event:** SpartHill Hacks 2026 — IBM Responsible AI Track
**Status:** Approved — Phase 1 Finalized

---

## Overview

This plan covers the backend infrastructure for EDIResolve AI. The backend is a pure Python library (no HTTP server) that the Streamlit frontend calls directly as functions. It owns:

- Shared Pydantic schemas consumed by all teammates
- Normalized JSON fixtures so development can proceed without live EDI files
- A reference data layer (UOM conversions, price contracts, product master)
- A deterministic reconciliation engine using `decimal.Decimal`
- A provider-independent AI explanation interface with a mock implementation
- A SQLite audit store with append-only writes and atomic action persistence
- An orchestration layer that sequences the above into a single `run_reconciliation()` call
- Integration and unit tests covering all 24 P0 acceptance tests (AT01–AT24)

**Out of scope for this plan:**
- Streamlit UI (Software Engineering teammate)
- Raw X12 EDI parsing (Data Science teammate)
- AI model integration beyond the mock (confirmed after watsonx access is granted)
- P2 three-way matching (AT25–AT36) — tracked as a stretch goal task at the end

**Key architectural decisions recorded here:**
- Input format: normalized Python dicts / JSON produced by the Data Science parser; backend validates with Pydantic
- AI provider: abstracted behind a protocol; `MockAIExplainer` ships first; Data Science teammate owns the real implementation
- Financial arithmetic: `decimal.Decimal` with `ROUND_HALF_UP`, USD two decimal places, zero tolerance baseline
- Deployment: `streamlit run app.py` locally; no HTTP layer needed
- `three_way_enabled` feature flag defaults to `False`; P2 three-way matching is deferred until AT01–AT24 pass
- Frontend imports only `backend.orchestrator` — never `comparator`, `audit_db`, or `ai_explainer` directly
- `case_version` is a monotonically increasing integer per case; `run_id` (UUID) is the separate reconciliation run identifier
- Reference data stubs owned by backend engineer; Data Science team owns final product master and validated conversions

---

## Module Map

```
backend/
  __init__.py
  models/
    __init__.py
    common.py         # shared enums, Evidence, AuditEvent
    documents.py      # PurchaseOrder, Invoice, ReceivingAdvice
    reference.py      # ProductMaster, UOMConversion, PriceContract
    reconciliation.py # ReconciliationCase, LineResult, CaseResult
    review.py         # ReviewAction, ReviewRequest, ReviewRecord
  reference_store.py  # load / query reference data from data/reference/
  comparator.py       # deterministic reconciliation rules (sections 18-19 of PRD)
  ai_explainer.py     # AIExplainerProtocol + MockAIExplainer
  audit_db.py         # SQLite schema, append-only writes, atomic action persistence
  orchestrator.py     # run_reconciliation() — sequences all components
  config.py           # Settings loaded from .env

data/
  reference/
    uom_conversions.json
    product_master.json
    price_contracts.json
  fixtures/
    AT01_matched.json
    AT02_uom_conversion.json
    AT03_missing_conversion.json
    AT04_price_variance.json
    ... (one file per AT01–AT12 for P0 development)

tests/
  conftest.py
  test_models.py
  test_reference_store.py
  test_comparator.py
  test_audit_db.py
  test_orchestrator.py
  test_acceptance.py   # AT01–AT24

.env.example
requirements.txt
```

---

## Phase 1 — Shared Schemas and Project Foundation

**Intent:** Establish the shared data contracts that every teammate depends on. Until these exist, no component can be developed independently. This is the highest-leverage task.

**Status:** `[x] complete — 23/23 tests pass`

---

### 1.1 Files to Create or Modify

| File | Action | Purpose |
|------|--------|---------|
| `requirements.txt` | Modify | Declare all runtime and dev dependencies |
| `.env.example` | Create | Document every environment variable with descriptions |
| `backend/__init__.py` | Modify | Empty; already exists — no changes needed |
| `backend/models/__init__.py` | Create | Re-export all public model names |
| `backend/models/common.py` | Create | Shared enums, `Money`, `Evidence`, `AuditEvent` |
| `backend/models/documents.py` | Create | `POLine`, `PurchaseOrder`, `InvoiceLine`, `Invoice`, `ReceivingAdviceLine`, `ReceivingAdvice` |
| `backend/models/reference.py` | Create | `UOMConversion`, `PriceContract`, `ProductMaster` |
| `backend/models/reconciliation.py` | Create | `ReconciliationPolicy`, `LineResult`, `CaseResult` |
| `backend/models/review.py` | Create | `ReviewRequest`, `ReviewRecord` |
| `backend/config.py` | Create | `Settings` loaded from `.env` via pydantic-settings |
| `tests/__init__.py` | Create | Empty; makes tests a package |
| `tests/test_models.py` | Create | Phase 1 unit tests |

---

### 1.2 `requirements.txt` — Full Contents

```
# Runtime
pydantic>=2.5,<3
pydantic-settings>=2.1,<3
python-dotenv>=1.0,<2

# Testing
pytest>=7.4,<9
pytest-cov>=4.1,<6
```

No other runtime dependencies are needed for Phase 1. Subsequent phases will add nothing to `requirements.txt` because all other dependencies (`sqlite3`, `decimal`, `uuid`, `datetime`) are Python stdlib.

> **Note for Data Science teammates:** When you add the AI provider SDK (watsonx-ai, openai, etc.), append it to this file under a `# AI Provider` comment.

---

### 1.3 `.env.example` — Full Contents

```dotenv
# ── Database ──────────────────────────────────────────────
# Path to the SQLite audit database (relative to project root)
DATABASE_PATH=data/edi_resolve.db

# ── AI Provider ───────────────────────────────────────────
# Options: "mock" | "mock_unavailable" | "watsonx" | "openai"
# Use "mock" for all development until AI provider credentials are confirmed.
AI_PROVIDER=mock

# Required when AI_PROVIDER=watsonx
# AI_API_KEY=
# AI_MODEL_ID=

# Required when AI_PROVIDER=openai
# OPENAI_API_KEY=

# ── Feature Flags ─────────────────────────────────────────
# Set to true only after P0 AT01–AT24 pass and PM approves P2 enablement.
THREE_WAY_ENABLED=false

# ── Retry Policy ──────────────────────────────────────────
# Maximum attempts for transient service errors before TECHNICAL_HOLD.
MAX_RETRY_ATTEMPTS=3
```

---

### 1.4 `backend/config.py`

**Class:** `Settings(BaseSettings)`

| Field | Type | Default | Source |
|-------|------|---------|--------|
| `database_path` | `str` | `"data/edi_resolve.db"` | `DATABASE_PATH` env var |
| `ai_provider` | `str` | `"mock"` | `AI_PROVIDER` env var |
| `ai_api_key` | `str \| None` | `None` | `AI_API_KEY` env var |
| `ai_model_id` | `str \| None` | `None` | `AI_MODEL_ID` env var |
| `three_way_enabled` | `bool` | `False` | `THREE_WAY_ENABLED` env var |
| `max_retry_attempts` | `int` | `3` | `MAX_RETRY_ATTEMPTS` env var |

- Uses `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`.
- Safe to instantiate with no `.env` file present — all defaults apply.
- No field may have a `float` type.

---

### 1.5 `backend/models/common.py`

#### `TransactionType` (str enum)

| Value | Meaning |
|-------|---------|
| `EDI_850` | Purchase Order |
| `EDI_810` | Invoice |
| `EDI_861` | Receiving Advice (ingestion only at P0; reconciliation gated by `three_way_enabled`) |

#### `DocumentStatus` (str enum)

| Value | Meaning |
|-------|---------|
| `VALID` | Passed all ingestion checks |
| `QUARANTINED` | Malformed envelope, unsupported version, or missing required field (AT11) |
| `DUPLICATE_CONFLICT` | Same business key, different content (AT09) |
| `REPLAY_NOOP` | Identical content hash — already processed (AT09) |

#### `FindingStatus` (str enum)

All values from PRD section 19. **`NOT_EVALUATED` and `CHECK_FAILED` are distinct values — they must never be aliases of each other.**

| Value | Meaning |
|-------|---------|
| `MATCHED_2WAY` | All applicable P0 checks pass |
| `MATCHED_3WAY` | All P0 + P2 receipt checks pass (P2 only) |
| `PRICE_VARIANCE` | Normalized unit price differs beyond tolerance |
| `QUANTITY_VARIANCE` | Cumulative invoice quantity exceeds PO authorization |
| `PARTIAL_BILLING` | Invoice quantity below remaining PO balance; explicit allocation recorded |
| `INSUFFICIENT_EVIDENCE` | Required reference data missing, expired, or contradictory |
| `WAIT_DOCUMENT` | PO or required document not found |
| `WAIT_RECEIPT` | P2 enabled; no valid receipt received yet |
| `REVIEW_LINK` | Ambiguous product or line match; analyst must select |
| `RECEIPT_VARIANCE` | Accepted receipt quantity below invoice quantity (P2) |
| `OVER_RECEIPT` | Accepted receipt exceeds PO authorization (P2) |
| `RECEIPT_REVIEW` | Ambiguous receipt status (damage, correction, unknown qualifier) (P2) |
| `NOT_EVALUATED` | Check skipped because a prerequisite was missing or blocked — NOT the same as a failure |
| `CHECK_FAILED` | Check ran and produced a definitive failure result |
| `TECHNICAL_HOLD` | Internal error; safe retry required |

#### `ReviewActionType` (str enum)

| Value | Meaning |
|-------|---------|
| `APPROVE` | Analyst accepts the proposed next step; does not release payment or modify external systems |
| `REJECT` | Analyst rejects the recommendation; case remains open |
| `ESCALATE` | Analyst assigns to an accountable owner with a reason and next action |

#### `ReceiptEventType` (str enum)

| Value | Meaning |
|-------|---------|
| `INCREMENTAL` | This event adds to running total |
| `CUMULATIVE` | This event is the current full snapshot; prior snapshots are superseded |

#### `Money` (Pydantic model, not a primitive)

- Stores exactly one field: `amount: Decimal`
- Constructor accepts `str` or `Decimal` input **only** — rejects `int` and `float` at validation time
- `__str__` returns `str(self.amount)` (e.g., `"2.00"`)
- Class method `from_str(s: str) -> Money` — parses a string, quantizes to two decimal places using `ROUND_HALF_UP`
- `__add__`, `__sub__`, `__neg__`, `__abs__` — all return `Money`; arithmetic stays in `Decimal`
- `__eq__` compares `amount` values using `Decimal` equality (no float comparison)
- Validator: raise `ValueError("Money does not accept float; use str or Decimal")` if a `float` is passed

> **Why `str`-only construction matters:** JSON deserialization passes numeric values as Python `float` by default. By rejecting `float`, any fixture or API caller that forgets to quote the number fails loudly at the model boundary, not silently inside a calculation.

#### `Evidence` (Pydantic model)

| Field | Type | Description |
|-------|------|-------------|
| `evidence_id` | `str` | Unique ID for this piece of evidence; referenced by AI explainer |
| `source_doc_id` | `str` | Which document this field came from (PO ID, invoice ID, contract ID) |
| `field_name` | `str` | Canonical field name (e.g., `"unit_price"`, `"uom"`) |
| `raw_value` | `str` | Original value as it appeared in the source document |
| `normalized_value` | `str \| None` | Value after normalization (e.g., after UOM conversion) |

#### `AuditEvent` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | `str` | Yes | UUID; unique per event |
| `case_id` | `str` | Yes | Which reconciliation case |
| `correlation_key` | `str` | Yes | Idempotency key; same action retry has same key |
| `actor` | `str` | Yes | Service name or reviewer identifier |
| `utc_timestamp` | `datetime` | Yes | UTC; `timezone=utc` enforced |
| `event_type` | `str` | Yes | e.g., `"RECONCILIATION_RUN"`, `"REVIEW_ACTION"`, `"TECHNICAL_HOLD"` |
| `before_state` | `str \| None` | No | JSON-serialized previous case state |
| `after_state` | `str \| None` | No | JSON-serialized new case state |
| `doc_hashes` | `dict[str, str] \| None` | No | `{doc_id: sha256_hex}` |
| `source_pointers` | `list[str] \| None` | No | Segment/field references from raw source |
| `findings` | `str \| None` | No | JSON-serialized `list[LineResult]` |
| `reason` | `str \| None` | No | Reviewer-provided reason (required when `event_type == "REVIEW_ACTION"`) |
| `escalation_owner` | `str \| None` | No | Required when action is `ESCALATE` |
| `policy_version` | `str \| None` | No | Identifies the reconciliation policy used |

---

### 1.6 `backend/models/documents.py`

#### `POLine` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `line_id` | `str` | Yes | PO-unique line identifier |
| `product_id` | `str` | Yes | Qualified product identifier (e.g., SKU, UPC) |
| `ordered_qty` | `Decimal` | Yes | Must be > 0 |
| `uom` | `str` | Yes | Unit of measure code (e.g., `"EA"`, `"CS"`) |
| `unit_price` | `Money` | Yes | Price per `price_basis` units of `uom` |
| `price_basis` | `int` | Yes | How many UOM units the price covers; default `1`; must be > 0 |
| `currency` | `str` | Yes | ISO 4217 currency code (e.g., `"USD"`) |

#### `PurchaseOrder` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `po_id` | `str` | Yes | Trading-partner-unique PO number |
| `order_date` | `date` | Yes | The date used for reference data effective-date lookups |
| `buyer_id` | `str` | Yes | Buyer identifier |
| `supplier_id` | `str` | Yes | Supplier identifier |
| `currency` | `str` | Yes | Document-level currency (lines may differ; comparator checks per line) |
| `lines` | `list[POLine]` | Yes | At least one line required |
| `content_hash` | `str` | Yes | `"sha256:<hex>"` of original source bytes; used for deduplication |
| `source_path` | `str \| None` | No | Path to raw source file, for provenance |

#### `InvoiceLine` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `line_id` | `str` | Yes | Invoice-unique line identifier |
| `po_reference` | `str` | Yes | PO ID this line references |
| `line_reference` | `str` | Yes | PO line ID this line references |
| `product_id` | `str` | Yes | Must match the referenced PO line's `product_id` |
| `invoiced_qty` | `Decimal` | Yes | Must be > 0 |
| `uom` | `str` | Yes | UOM code |
| `unit_price` | `Money` | Yes | Invoice unit price |
| `price_basis` | `int` | Yes | Must be > 0; default `1` |
| `currency` | `str` | Yes | ISO 4217 |

#### `Invoice` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `invoice_id` | `str` | Yes | Invoice number |
| `invoice_date` | `date` | Yes | |
| `po_references` | `list[str]` | Yes | All PO IDs referenced (at least one) |
| `currency` | `str` | Yes | Document-level currency |
| `lines` | `list[InvoiceLine]` | Yes | At least one line |
| `content_hash` | `str` | Yes | `"sha256:<hex>"` |
| `source_path` | `str \| None` | No | |

#### `ReceivingAdviceLine` (Pydantic model — P2 fields; included now so the schema is stable)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `line_id` | `str` | Yes | |
| `po_reference` | `str` | Yes | PO ID |
| `line_reference` | `str` | Yes | PO line ID |
| `product_id` | `str` | Yes | |
| `received_qty` | `Decimal` | Yes | Physical count received; >= 0 |
| `accepted_qty` | `Decimal` | Yes | Quantity accepted for billing; >= 0; <= `received_qty` |
| `rejected_qty` | `Decimal` | Yes | >= 0 |
| `damaged_qty` | `Decimal` | Yes | >= 0; may overlap with `rejected_qty` depending on partner profile |
| `uom` | `str` | Yes | |
| `receipt_event_type` | `ReceiptEventType` | Yes | `INCREMENTAL` or `CUMULATIVE` |

#### `ReceivingAdvice` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `receipt_id` | `str` | Yes | |
| `receipt_date` | `date` | Yes | |
| `po_references` | `list[str]` | Yes | |
| `facility` | `str \| None` | No | Receiving facility identifier |
| `lines` | `list[ReceivingAdviceLine]` | Yes | At least one |
| `content_hash` | `str` | Yes | |
| `source_path` | `str \| None` | No | |

---

### 1.7 `backend/models/reference.py`

#### `UOMConversion` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `from_uom` | `str` | Yes | Source UOM code (e.g., `"CS"`) |
| `to_uom` | `str` | Yes | Target base UOM (e.g., `"EA"`) |
| `factor` | `Decimal` | Yes | Number of `to_uom` units per one `from_uom` unit; must be > 0 |
| `product_id` | `str \| None` | No | If set, applies only to this product; `None` = universal |
| `effective_date` | `date` | Yes | Inclusive start of validity |
| `expiry_date` | `date \| None` | No | Inclusive end; `None` = no expiry |
| `source` | `str` | Yes | e.g., `"product_master_v1"` |
| `approver` | `str \| None` | No | |

#### `PriceContract` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `contract_id` | `str` | Yes | |
| `product_id` | `str` | Yes | |
| `agreed_price` | `Money` | Yes | |
| `price_basis` | `int` | Yes | Must be > 0 |
| `currency` | `str` | Yes | |
| `effective_from` | `date` | Yes | |
| `effective_to` | `date \| None` | No | |
| `partner_scope` | `str \| None` | No | If set, applies only to this buyer/supplier pair |
| `policy_version` | `str` | Yes | |

#### `ProductMaster` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `product_id` | `str` | Yes | Canonical internal ID |
| `description` | `str` | Yes | Human-readable product name |
| `uom_conversions` | `list[UOMConversion]` | Yes | May be empty; absence triggers `INSUFFICIENT_EVIDENCE` for UOM checks |
| `identifiers` | `dict[str, str]` | Yes | e.g., `{"UPC": "012345678901", "buyer_sku": "X123"}` |

---

### 1.8 `backend/models/reconciliation.py`

#### `ReconciliationPolicy` (Pydantic model)

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `currency` | `str` | Yes | — | `"USD"` for prototype |
| `decimal_places` | `int` | Yes | — | `2` for USD |
| `rounding_mode` | `str` | Yes | — | `"ROUND_HALF_UP"` |
| `price_abs_tolerance` | `Money` | Yes | — | `Money("0.00")` = zero tolerance baseline |
| `price_rel_tolerance` | `Decimal` | Yes | — | `Decimal("0")` = zero relative tolerance |
| `qty_tolerance` | `Decimal` | Yes | — | `Decimal("0")` |
| `three_way_enabled` | `bool` | Yes | `False` | Must match `Settings.three_way_enabled` |
| `policy_version` | `str` | Yes | — | Identifies this policy configuration; retained in every audit event |

#### `LineResult` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `line_id` | `str` | Yes | Unique ID for this result row |
| `po_line_id` | `str` | Yes | |
| `invoice_line_id` | `str` | Yes | |
| `status` | `FindingStatus` | Yes | Per-line outcome |
| `delta_price` | `Money \| None` | No | `None` when price check was `NOT_EVALUATED` |
| `delta_qty` | `Decimal \| None` | No | `None` when qty check was `NOT_EVALUATED` |
| `normalized_po_qty` | `Decimal \| None` | No | Quantity after UOM normalization; `None` if conversion missing |
| `normalized_invoice_qty` | `Decimal \| None` | No | |
| `po_unit_price_base` | `Money \| None` | No | PO price normalized to base UOM |
| `invoice_unit_price_base` | `Money \| None` | No | Invoice price normalized to base UOM |
| `contract_price_base` | `Money \| None` | No | Contract price displayed separately; never overwrites PO price |
| `allocated_qty` | `Decimal` | Yes | Quantity allocated from this invoice line against the PO |
| `evidence` | `list[Evidence]` | Yes | All field-level pointers supporting this result |
| `checks_not_evaluated` | `list[str]` | Yes | Names of checks skipped (e.g., `["price_check", "qty_check"]`) |
| `ai_explanation` | `str \| None` | No | Populated by orchestrator after AI call |

#### `CaseResult` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `case_id` | `str` | Yes | UUID; stable across runs for the same PO+invoice pair |
| `run_id` | `str` | Yes | UUID; unique per reconciliation run |
| `case_version` | `int` | Yes | Monotonically increasing integer per case; starts at `1` |
| `po_id` | `str` | Yes | |
| `invoice_id` | `str` | Yes | |
| `overall_status` | `FindingStatus` | Yes | Worst-case status across all line results |
| `line_results` | `list[LineResult]` | Yes | One entry per matched PO+invoice line pair |
| `policy_version` | `str` | Yes | |
| `run_timestamp` | `datetime` | Yes | UTC |
| `ai_result_status` | `str \| None` | No | `"OK"`, `"AI_ABSTAIN"`, `"AI_UNAVAILABLE"`, or `None` if not yet called |

---

### 1.9 `backend/models/review.py`

#### `ReviewRequest` (Pydantic model)

| Field | Type | Required | Validation |
|-------|------|----------|------------|
| `case_id` | `str` | Yes | |
| `case_version` | `int` | Yes | Must be > 0; used for optimistic lock check in orchestrator |
| `action` | `ReviewActionType` | Yes | |
| `reason` | `str` | Yes | Must be non-empty after stripping whitespace |
| `escalation_owner` | `str \| None` | No | Required when `action == ESCALATE`; enforced by validator |

- Model validator: if `action == ReviewActionType.ESCALATE` and `escalation_owner` is `None` or blank → `ValueError("escalation_owner is required for ESCALATE actions")`

#### `ReviewRecord` (Pydantic model)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `review_id` | `str` | Yes | UUID |
| `case_id` | `str` | Yes | |
| `case_version` | `int` | Yes | Version at time of review |
| `action` | `ReviewActionType` | Yes | |
| `reason` | `str` | Yes | |
| `escalation_owner` | `str \| None` | No | |
| `utc_timestamp` | `datetime` | Yes | UTC |
| `audit_event_id` | `str` | Yes | FK to the `AuditEvent` written atomically with this record |

---

### 1.10 `backend/models/__init__.py` — Public Exports

The following names must be importable from `backend.models` directly:

```
# Enums
TransactionType, DocumentStatus, FindingStatus, ReviewActionType, ReceiptEventType

# Value types
Money

# Supporting models
Evidence, AuditEvent

# Document models
POLine, PurchaseOrder
InvoiceLine, Invoice
ReceivingAdviceLine, ReceivingAdvice

# Reference models
UOMConversion, PriceContract, ProductMaster

# Reconciliation models
ReconciliationPolicy, LineResult, CaseResult

# Review models
ReviewRequest, ReviewRecord
```

---

### 1.11 Phase 1 Unit Tests (`tests/test_models.py`)

The following tests must all pass before Phase 1 is considered complete.

#### Group A — `Money`

| Test | Assertion |
|------|-----------|
| `test_money_accepts_decimal` | `Money(amount=Decimal("2.00"))` constructs without error |
| `test_money_accepts_string` | `Money.from_str("2.00")` returns `Money` with `amount == Decimal("2.00")` |
| `test_money_rejects_float` | `Money(amount=2.0)` raises `ValidationError` |
| `test_money_rejects_int` | `Money(amount=2)` raises `ValidationError` |
| `test_money_arithmetic_stays_decimal` | `Money.from_str("1.00") + Money.from_str("0.10")` returns `Money` with `amount == Decimal("1.10")`; result is not a `float` |
| `test_money_from_str_rounds` | `Money.from_str("2.335")` returns `amount == Decimal("2.34")` (ROUND_HALF_UP) |
| `test_money_equality` | `Money.from_str("2.00") == Money.from_str("2.00")` is `True` |

#### Group B — `FindingStatus`

| Test | Assertion |
|------|-----------|
| `test_not_evaluated_distinct_from_check_failed` | `FindingStatus.NOT_EVALUATED != FindingStatus.CHECK_FAILED` |
| `test_all_status_values_are_strings` | Every member of `FindingStatus` has a `str` value (for SQLite storage) |
| `test_finding_status_serializes_to_string` | `json.dumps({"s": FindingStatus.MATCHED_2WAY})` does not raise; value is `"MATCHED_2WAY"` |

#### Group C — `ReviewRequest` validators

| Test | Assertion |
|------|-----------|
| `test_review_request_requires_reason` | Empty `reason=""` raises `ValidationError` |
| `test_escalate_requires_owner` | `action=ESCALATE`, `escalation_owner=None` raises `ValidationError` |
| `test_escalate_with_owner_valid` | `action=ESCALATE`, `escalation_owner="ops_manager"` constructs without error |
| `test_approve_does_not_require_owner` | `action=APPROVE`, `escalation_owner=None` constructs without error |

#### Group D — `CaseResult` version field

| Test | Assertion |
|------|-----------|
| `test_case_result_version_is_int` | `CaseResult.case_version` field type is `int`; value `1` accepted |
| `test_run_id_distinct_from_case_id` | A `CaseResult` can be constructed with different `case_id` and `run_id` values |

#### Group E — `Settings`

| Test | Assertion |
|------|-----------|
| `test_settings_defaults` | `Settings()` with no `.env` file: `ai_provider == "mock"`, `three_way_enabled == False`, `max_retry_attempts == 3` |
| `test_settings_loads_env_file` | Write a temp `.env` with `AI_PROVIDER=openai`; `Settings(_env_file=tmp_path)` reads it correctly |
| `test_three_way_enabled_default_false` | `Settings().three_way_enabled is False` |

#### Group F — Model round-trip serialization

| Test | Assertion |
|------|-----------|
| `test_purchase_order_roundtrip` | Construct a `PurchaseOrder` with one `POLine`; serialize to JSON; deserialize back; all fields equal |
| `test_invoice_roundtrip` | Same pattern for `Invoice` + `InvoiceLine` |
| `test_money_survives_json_roundtrip` | A `Money` field in a model serializes to a JSON string (not a float) and deserializes back to the same `Decimal` value |

---

### 1.12 Integration Points After Phase 1

**For the Data Science teammate (parser output contract):**

```python
from backend.models import PurchaseOrder, POLine, Invoice, InvoiceLine, Money
from decimal import Decimal
from datetime import date

# Construct after parsing raw X12 850
po = PurchaseOrder(
    po_id="PO-12345",
    order_date=date(2026, 10, 1),
    buyer_id="BUYER-001",
    supplier_id="SUP-001",
    currency="USD",
    lines=[
        POLine(
            line_id="1",
            product_id="SKU-ABC",
            ordered_qty=Decimal("100"),
            uom="EA",
            unit_price=Money(amount=Decimal("2.00")),
            price_basis=1,
            currency="USD",
        )
    ],
    content_hash="sha256:abc123",
    source_path="data/raw/PO-12345.edi",
)
```

Rule: if a required field cannot be extracted from the X12 document, do **not** pass `None` or a zero default — raise a `ValueError` with the field name. The orchestrator catches this and produces a `QUARANTINED` result.

**For the Software Engineering teammate (frontend contract):**

```python
from backend.models import CaseResult, FindingStatus, ReviewRequest, ReviewActionType
# CaseResult.overall_status → drives status badge color
# CaseResult.line_results → drives the discrepancy table
# CaseResult.case_version → must be passed back in ReviewRequest for optimistic locking
```

The frontend never imports from `backend.comparator`, `backend.audit_db`, or `backend.ai_explainer`.

---

### 1.13 Phase 1 Completion Criteria

Phase 1 is complete when **all of the following are true**:

- [ ] `requirements.txt` is populated and `pip install -r requirements.txt` succeeds in a clean venv
- [ ] `.env.example` exists with all variables documented
- [ ] All five `backend/models/*.py` files exist and are importable
- [ ] `backend/config.py` exists and `Settings()` instantiates with no `.env` present
- [ ] `pytest tests/test_models.py -v` passes with zero failures
- [ ] `python -c "from backend.models import PurchaseOrder, CaseResult, ReviewRequest, Money"` exits 0
- [ ] No `float` appears anywhere in the model definitions

---

## Phase 2 — Reference Data Store and Fixtures

**Intent:** Provide the reference data (UOM conversions, product master, price contracts) and minimal JSON fixtures needed so the comparator and tests can run without waiting for live Data Science output.

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `data/reference/uom_conversions.json` | Verified pack conversions (e.g., 12 EA per CS) |
| `data/reference/product_master.json` | Product IDs, descriptions, identifier cross-references |
| `data/reference/price_contracts.json` | Agreed prices with effective date ranges |
| `data/fixtures/AT01_matched.json` | Clean two-way match (AT01) |
| `data/fixtures/AT02_uom_cs_to_ea.json` | 50 CS vs 600 EA with verified conversion (AT02) |
| `data/fixtures/AT03_missing_conversion.json` | Same as AT02 but conversion absent (AT03) |
| `data/fixtures/AT04_price_variance.json` | $2.00 PO vs $2.10 invoice (AT04) |
| `data/fixtures/AT05_contract_stale_price.json` | AT04 + valid contract at $2.10 (AT05) |
| `data/fixtures/AT07_partial_billing.json` | 40 of 100 EA invoiced (AT07) |
| `data/fixtures/AT08_quantity_overrun.json` | Cumulative 110 of 100 EA (AT08) |
| `data/fixtures/AT11_malformed.json` | Missing quantity field (AT11) |
| `data/fixtures/AT12_currency_mismatch.json` | PO in USD, invoice in CAD (AT12) |
| `backend/reference_store.py` | Load and query reference data |

### `backend/reference_store.py` Functions

```
load_uom_conversions(path: str) -> list[UOMConversion]
load_product_master(path: str) -> dict[str, ProductMaster]
load_price_contracts(path: str) -> list[PriceContract]

get_uom_conversion(
    from_uom: str,
    to_uom: str,
    product_id: str | None,
    as_of_date: date,
    conversions: list[UOMConversion],
) -> UOMConversion | None   # None = INSUFFICIENT_EVIDENCE

get_active_contracts(
    product_id: str,
    as_of_date: date,
    contracts: list[PriceContract],
) -> list[PriceContract]    # empty = no contract; >1 = contradictory (flag)
```

### Reference Data Schema (JSON)

`uom_conversions.json` — array of objects matching `UOMConversion` fields.  
`product_master.json` — object keyed by `product_id`, values matching `ProductMaster`.  
`price_contracts.json` — array of objects matching `PriceContract` fields.

Fixtures follow the canonical JSON serialization of `PurchaseOrder` and `Invoice` Pydantic models. Each fixture file contains `{ "po": {...}, "invoice": {...}, "label": "AT02", "expected_status": "MATCHED_2WAY" }`.

### Integration Points

- `reference_store.py` is called by `comparator.py` at runtime.
- Fixtures are loaded by `tests/conftest.py` and passed to comparator tests.
- Data Science teammate can replace fixtures with real parsed outputs without touching the comparator.

### Acceptance Tests

- `get_uom_conversion` returns correct factor for a known product on a valid date.
- Returns `None` when conversion is absent, expired, or has conflicting records for the same date range.
- `get_active_contracts` returns `[]` for no contract and flags multiple overlapping contracts.
- All fixture JSON files deserialize into valid Pydantic models without validation errors.

---

## Phase 3 — Deterministic Comparator Engine

**Intent:** Implement the core reconciliation rules from PRD sections 18–19. This is the financial heart of the system. All calculations use `Decimal`; no `float` is permitted.

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `backend/comparator.py` | All deterministic reconciliation logic |
| `tests/test_comparator.py` | Unit tests per acceptance test scenario |

### `backend/comparator.py` Functions

```
normalize_quantity(
    qty: Decimal,
    from_uom: str,
    to_base_uom: str,
    conversion: UOMConversion | None,
) -> Decimal | None   # None = INSUFFICIENT_EVIDENCE

normalize_unit_price(
    price: Money,
    from_uom: str,
    to_base_uom: str,
    price_basis: int,
    conversion: UOMConversion | None,
) -> Money | None     # None = INSUFFICIENT_EVIDENCE

compare_line(
    po_line: POLine,
    invoice_line: InvoiceLine,
    conversion: UOMConversion | None,
    contracts: list[PriceContract],
    policy: ReconciliationPolicy,
    allocated_qty: Decimal,          # from prior invoices on this PO line
) -> LineResult

compare_case(
    po: PurchaseOrder,
    invoice: Invoice,
    reference_store: ReferenceStore,  # or explicit lists
    policy: ReconciliationPolicy,
    prior_allocations: dict[str, Decimal],  # po_line_id -> previously allocated qty
) -> CaseResult
```

### Calculation Rules (must match PRD section 18)

- `Q_base = Q_raw × f` where `f` = verified factor from `UOMConversion`
- `P_base = P_raw / (price_basis × f)`
- `delta_price = P_invoice_base - P_PO_base`
- `delta_qty = Q_invoice_base - Q_PO_allocated_base`
- Line extension: `round(Q_invoice_base × P_PO_base, 2, ROUND_HALF_UP)`
- Price acceptance: `abs(delta_price) <= max(abs_tolerance, abs(P_PO_base) × rel_tolerance)`
- Quantity acceptance: `abs(delta_qty) <= qty_tolerance`
- Zero tolerance baseline (AT13, AT14 must pass with exact boundary behavior)
- Currency mismatch on a line → that line's price check is `NOT_EVALUATED` (held)
- Unknown UOM factor or price basis → `INSUFFICIENT_EVIDENCE`; dependent checks are `NOT_EVALUATED`, not `CHECK_FAILED`
- `PARTIAL_BILLING` when invoice qty < PO remaining and explicit allocation exists
- `QUANTITY_VARIANCE` when cumulative invoice qty > PO authorization

### Integration Points

- Receives `PurchaseOrder` and `Invoice` from the orchestrator (originally from Data Science).
- Receives `UOMConversion` and `PriceContract` from `reference_store.py`.
- Returns `CaseResult` with `LineResult` list; the orchestrator passes this to the AI explainer and audit store.
- Streamlit frontend reads `CaseResult.line_results` to render the discrepancy table.

### Acceptance Tests

| AT | Assertion |
|----|-----------|
| AT01 | `MATCHED_2WAY`, zero delta, receipt `NOT_EVALUATED` |
| AT02 | 50 CS / 600 EA both normalize; no UOM or qty variance |
| AT03 | `INSUFFICIENT_EVIDENCE` on missing conversion; dependent checks `NOT_EVALUATED` |
| AT04 | `PRICE_VARIANCE`, delta = +$0.10, extension delta = +$10 |
| AT05 | AT04 result unchanged; contract shown as separate evidence, not as resolution |
| AT07 | `PARTIAL_BILLING`; PO balance = 60 EA; not a shortage claim |
| AT08 | `QUANTITY_VARIANCE`; cumulative = 110; no duplicate allocation |
| AT12 | Currency mismatch → price check held; unsupported total `NOT_EVALUATED` |
| AT13 | `$0.335 × 3 = $1.01`; `$0.10/EA × 20 EA = $2.00`; no float drift |
| AT14 | Boundary `$0.01` passes; `$0.0101` fails; policy version retained |

---

## Phase 4 — SQLite Audit Store

**Intent:** Implement the append-only audit database. Human review actions must be persisted atomically: if the write fails, the action must not be acknowledged. This is a hard requirement from PRD section 20.2 (AT19).

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `backend/audit_db.py` | SQLite schema, initialization, and all read/write operations |
| `tests/test_audit_db.py` | Unit tests including atomic failure simulation |

### `backend/audit_db.py` Functions and Schema

**Schema (three tables):**

```sql
-- append-only; never UPDATE or DELETE
CREATE TABLE audit_events (
    event_id        TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    correlation_key TEXT NOT NULL,
    actor           TEXT NOT NULL,
    utc_timestamp   TEXT NOT NULL,   -- ISO 8601
    event_type      TEXT NOT NULL,
    before_state    TEXT,            -- JSON
    after_state     TEXT,            -- JSON
    doc_hashes      TEXT,            -- JSON
    source_pointers TEXT,            -- JSON
    findings        TEXT,            -- JSON
    reason          TEXT,
    escalation_owner TEXT,
    policy_version  TEXT
);

CREATE TABLE review_records (
    review_id        TEXT PRIMARY KEY,
    case_id          TEXT NOT NULL,
    case_version     TEXT NOT NULL,
    action           TEXT NOT NULL,   -- APPROVE / REJECT / ESCALATE
    reason           TEXT NOT NULL,
    escalation_owner TEXT,
    utc_timestamp    TEXT NOT NULL,
    audit_event_id   TEXT NOT NULL REFERENCES audit_events(event_id)
);

CREATE TABLE reconciliation_runs (
    run_id          TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL,
    po_id           TEXT NOT NULL,
    invoice_id      TEXT NOT NULL,
    overall_status  TEXT NOT NULL,
    case_result_json TEXT NOT NULL,  -- full CaseResult serialized
    policy_version  TEXT NOT NULL,
    run_timestamp   TEXT NOT NULL
);
```

**Functions:**

```
init_db(db_path: str) -> None
    # Creates tables if not exist; safe to call on startup

append_audit_event(event: AuditEvent, conn: Connection) -> None
    # Always appends; never mutates existing rows

save_review_action(
    request: ReviewRequest,
    case_result: CaseResult,
    conn: Connection,
) -> ReviewRecord
    # Writes review_records + audit_events in one transaction.
    # On any failure: rolls back both, raises AuditPersistenceError.
    # Caller catches and shows "Action not saved" to user (AT19).

save_reconciliation_run(result: CaseResult, conn: Connection) -> None

get_case_history(case_id: str, conn: Connection) -> list[AuditEvent]
    # Returns all events for a case in chronological order (AT09, AT22)

get_review_records(case_id: str, conn: Connection) -> list[ReviewRecord]

get_exception_summary(conn: Connection) -> list[dict]
    # Aggregates status counts by FindingStatus for P1 dashboard (US-10)
```

**`AuditPersistenceError`** — custom exception raised when atomic write fails; never silently swallowed.

### Integration Points

- Called exclusively from `orchestrator.py` (never called directly from Streamlit).
- Software Engineering teammate catches `AuditPersistenceError` and displays the "Action not saved" message.
- `get_exception_summary()` feeds the P1 dashboard without needing additional schema changes.

### Acceptance Tests

| AT | Assertion |
|----|-----------|
| AT09 | Duplicate content hash returns existing case; conflicting content same key → `DUPLICATE_CONFLICT` |
| AT17 | Three distinct workflow states (approve/reject/escalate) each written with reason |
| AT18 | Stale case version rejected before write; reason required enforced at model level |
| AT19 | Simulated write failure → `AuditPersistenceError`; no partial record; retry commits exactly once |
| AT22 | After evidence correction, old recommendation and prior decisions remain retrievable |

---

## Phase 5 — AI Explanation Interface

**Intent:** Define a provider-independent `AIExplainerProtocol` and ship a `MockAIExplainer` that returns deterministic, evidence-grounded responses. The real watsonx/OpenAI implementation drops in later without changing anything else.

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `backend/ai_explainer.py` | Protocol definition + mock implementation |
| `tests/test_ai_explainer.py` | Tests for mock behavior and protocol contract |

### `backend/ai_explainer.py`

**`AIExplainerProtocol` (runtime checkable Protocol):**

```
def explain(self, bundle: ExplanationBundle) -> AIExplanationResult: ...
```

**`ExplanationBundle`** — the evidence package sent to the model (PRD section 19):
- `case_id`
- `findings: list[LineResult]`
- `evidence: list[Evidence]` — source field values, verified references with validity dates
- `missing_facts: list[str]`
- `deterministic_status: FindingStatus`
- `allowed_actions: list[ReviewActionType]`

**`AIExplanationResult`:**
- `finding_ids: list[str]` — which findings this addresses
- `possible_cause: str` — labeled as hypothesis
- `cited_evidence_ids: list[str]` — must all exist in bundle; verified before use
- `uncertainty_note: str | None`
- `missing_evidence: list[str]`
- `suggested_next_action: ReviewActionType | None`
- `status: Literal["OK", "AI_ABSTAIN", "AI_UNAVAILABLE"]`

**`MockAIExplainer`:**
- Returns a deterministic explanation based on `deterministic_status` — e.g., `PRICE_VARIANCE` → "Possible cause: stale PO price. Verify contract."
- All `cited_evidence_ids` reference actual evidence IDs in the bundle (never invented).
- For `INSUFFICIENT_EVIDENCE` → returns `AI_ABSTAIN` with missing facts listed.
- Simulates `AI_UNAVAILABLE` when `Settings.AI_PROVIDER == "mock_unavailable"`.

**Validation rules (applied before returning result to orchestrator):**
- Every `cited_evidence_id` must exist in the bundle — discard narrative if not (AT15).
- Numeric values in the explanation must agree with `LineResult` deltas — discard if not.
- No suggested action outside `allowed_actions` — replace with `AI_ABSTAIN`.

### Integration Points

- `orchestrator.py` calls `explainer.explain(bundle)` after `compare_case()` returns.
- Data Science teammate implements `WatsonxAIExplainer` or `OpenAIExplainer` conforming to the same protocol; swapped in via `Settings.AI_PROVIDER`.
- The AI explainer never receives raw EDI bytes — only the structured `ExplanationBundle`.

### Acceptance Tests

| AT | Assertion |
|----|-----------|
| AT06 | No contract present → AI abstains on cause or requests verification; rules still show delta |
| AT15 | Invented contract ID in mock response → narrative discarded; `AI_ABSTAIN` returned |
| AT16 | `MockAIExplainer` with `mock_unavailable` → `AI_UNAVAILABLE`; `CaseResult` still accessible |

---

## Phase 6 — Orchestrator

**Intent:** Wire all components into `run_reconciliation()` — a single function the Streamlit frontend calls. Also implement `submit_review()` which handles human actions with atomic audit persistence.

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `backend/orchestrator.py` | `run_reconciliation()` and `submit_review()` |
| `tests/test_orchestrator.py` | End-to-end integration tests |

### `backend/orchestrator.py` Functions

```
run_reconciliation(
    po: PurchaseOrder,
    invoice: Invoice,
    settings: Settings,
    db_conn: Connection,
) -> CaseResult
    # 1. Validate inputs (Pydantic already ran at construction)
    # 2. Check content hashes for duplicate / replay (AT09)
    # 3. Load reference data via reference_store
    # 4. Run compare_case() → CaseResult
    # 5. Build ExplanationBundle; call explainer.explain()
    # 6. Attach AIExplanationResult to CaseResult
    # 7. save_reconciliation_run() → audit_db
    # 8. Return CaseResult
    # On any step failure → TECHNICAL_HOLD; append audit event; return held CaseResult

submit_review(
    request: ReviewRequest,
    db_conn: Connection,
) -> ReviewRecord
    # 1. Load current CaseResult from DB
    # 2. Validate case_version matches current (optimistic lock — AT18)
    # 3. Validate reason is non-empty (AT18)
    # 4. Validate action is not closure of unresolved findings (AT18)
    # 5. Call save_review_action() atomically
    # 6. On AuditPersistenceError → re-raise; caller shows "Action not saved"
    # Returns saved ReviewRecord

get_case_history(case_id: str, db_conn: Connection) -> list[AuditEvent]
    # Thin delegation to audit_db; exposed here so frontend imports one module

get_exception_summary(db_conn: Connection) -> list[dict]
    # For P1 dashboard
```

**Error handling policy:**
- Parser / reference / rules failures → `TECHNICAL_HOLD` status; audit event appended; no silent pass.
- AI failure → `AI_UNAVAILABLE`; deterministic `CaseResult` returned; reviewer can proceed.
- Audit failure during `submit_review` → `AuditPersistenceError` propagated; no partial state.

### Integration Points

- **Software Engineering teammate** imports `orchestrator.py` and calls `run_reconciliation()` and `submit_review()` — these are the only two entry points the frontend needs.
- The frontend never imports `comparator.py`, `audit_db.py`, or `ai_explainer.py` directly.
- `db_conn` is opened once in the Streamlit session and passed down; `init_db()` is called at app startup.

### Acceptance Tests

| AT | Assertion |
|----|-----------|
| AT01–AT12 | Full round-trip through orchestrator returns expected `overall_status` |
| AT17 | `submit_review()` for approve, reject, escalate each returns a distinct `ReviewRecord` |
| AT18 | Closure attempt with unresolved findings → exception; missing reason → exception |
| AT19 | Simulated audit write failure → `AuditPersistenceError`; case status unchanged |
| AT20 | Rules engine exception → `TECHNICAL_HOLD`; no pass; retry is idempotent |
| AT22 | After evidence correction, `run_reconciliation()` creates new version; old history retrievable |

---

## Phase 7 — Integration Tests and P0 Acceptance Suite

**Intent:** Verify all 24 P0 acceptance tests from PRD section 21 using the labeled fixtures from Phase 2. These are the release gate for P0 (PRD section 22.1).

**Status:** `[ ] pending`

### Files to Create

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared fixtures: DB connection, settings, reference store, explainer |
| `tests/test_acceptance.py` | AT01–AT24 parameterized tests |

### `tests/conftest.py` Fixtures

```python
@pytest.fixture
def settings() -> Settings:
    # Returns Settings with test DB path, mock AI, three_way_enabled=False

@pytest.fixture
def db_conn(settings, tmp_path) -> Connection:
    # In-memory or tmp SQLite; calls init_db()

@pytest.fixture
def reference_store(settings) -> ReferenceStore:
    # Loads from data/reference/

@pytest.fixture(params=["AT01", "AT02", ...])
def acceptance_fixture(request) -> dict:
    # Loads data/fixtures/{request.param}_*.json
```

### `tests/test_acceptance.py` Pattern

Each test:
1. Loads fixture → deserializes into `PurchaseOrder` + `Invoice`.
2. Calls `run_reconciliation()`.
3. Asserts `CaseResult.overall_status == fixture["expected_status"]`.
4. Asserts specific `LineResult` fields as specified in PRD section 21.
5. For review tests (AT17–AT19): also calls `submit_review()` and asserts audit records.

### Acceptance Gate

All of AT01–AT24 must pass before P0 is considered complete (PRD section 22.1).  
AT25–AT36 are only run when `three_way_enabled=True` and constitute the P2 stretch goal gate.

---

## Future Roadmap — P2 Three-Way Matching

**Status:** `[ ] pending — do not start until AT01–AT24 pass and PM approves enablement`

This phase is explicitly out of scope for the hackathon MVP. The `ReceivingAdvice` model is defined in Phase 1 so the schema is stable, but no reconciliation logic is built until P0 is complete.

Prerequisite checklist before starting:
- [ ] AT01–AT24 all pass
- [ ] PM and engineering lead record enablement decision and supported 861 partner profile
- [ ] `THREE_WAY_ENABLED=true` set in `.env`
- [ ] Accepted-vs-received semantics, incremental/cumulative handling, and correction rules are documented

When this phase begins, it will add `backend/receipt_ledger.py`, `tests/test_receipt_ledger.py`, and fixtures AT25–AT36.

---

## Interface Contracts for Teammates

### For the Data Science Teammate (Parser Owner)

After parsing raw X12 documents, wrap the output in these Pydantic models before passing anything to the backend. Import from `backend.models` — the flat namespace:

```python
from backend.models import PurchaseOrder, POLine, Invoice, InvoiceLine, Money
from decimal import Decimal
from datetime import date

po = PurchaseOrder(
    po_id="PO-12345",
    order_date=date(2026, 10, 1),
    buyer_id="BUYER-001",
    supplier_id="SUP-001",
    currency="USD",
    lines=[POLine(
        line_id="1",
        product_id="SKU-ABC",
        ordered_qty=Decimal("100"),
        uom="EA",
        unit_price=Money(amount=Decimal("2.00")),
        price_basis=1,
        currency="USD",
    )],
    content_hash="sha256:abc123",
    source_path="data/raw/PO-12345.edi",
)
```

If a required field cannot be extracted from the X12 document, raise `ValueError` — do not pass `None` or default to zero. The backend orchestrator catches this and produces a `QUARANTINED` result.

### For the Software Engineering Teammate (Streamlit Owner)

Your Streamlit app imports from exactly **one** backend module: `backend.orchestrator`. It also calls `init_db` once at startup:

```python
from backend.orchestrator import (
    run_reconciliation,
    submit_review,
    get_case_history,
    get_exception_summary,
    init_backend,          # convenience: calls init_db internally
)
from backend.models import CaseResult, FindingStatus, ReviewRequest, ReviewActionType
from backend.config import Settings
from backend.exceptions import AuditPersistenceError

# At app startup (call once)
settings = Settings()
conn = init_backend(settings)   # opens DB, creates tables, returns connection

# When analyst uploads documents
case_result: CaseResult = run_reconciliation(po, invoice, settings, conn)

# When analyst submits a review action
try:
    record = submit_review(request, conn)
    st.success("Decision saved.")
except AuditPersistenceError:
    st.error("Action not saved. Please try again.")

# P1 dashboard
summary = get_exception_summary(conn)  # list[dict] with status counts
```

Do **not** import from `backend.comparator`, `backend.audit_db`, or `backend.ai_explainer`.

---

## Execution Order and Dependencies

```
Phase 1 → Phase 2 → Phase 3 ──┐
  │           │                │
  │           └─→ Phase 4 ────┤→ Phase 6 → Phase 7
  │                            │
  └───────────→ Phase 5 ──────┘
```

- Phase 1 unblocks all teammates immediately.
- Phase 2 unblocks all tests and Data Science integration.
- Phases 3, 4, 5 can proceed in parallel after Phase 2.
- Phase 6 (orchestrator) requires 3, 4, and 5 to be complete.
- Phase 7 (acceptance tests) requires Phase 6.

**Ownership split for Phases 3 and 5:**
- Phase 3 (comparator interface + mock): backend engineer defines the interface and stub; Data Science teammate owns the production implementation.
- Phase 5 (AI explainer): backend engineer owns the protocol and mock; Data Science teammate implements the real `WatsonxAIExplainer` or equivalent.

---

## Recommended First Task

**Start with Phase 1 — Shared Schemas (`backend/models/`).**

**Why this first:**

1. **Unblocks every teammate immediately.** The Software Engineering teammate cannot build the review UI and the Data Science teammate cannot finalize their parser output until `PurchaseOrder`, `Invoice`, `CaseResult`, and `ReviewRequest` exist as importable, validated Python types.

2. **Zero external dependencies.** Phase 1 only needs `pydantic`, `pydantic-settings`, and Python stdlib (`decimal`, `enum`, `datetime`). No database, no AI provider, no reference data files are needed.

3. **Highest leverage per line of code.** Getting `Money` right — rejecting `float`, enforcing two-decimal USD — eliminates an entire class of financial bugs before any calculation is written.

4. **Defines the shared language.** `FindingStatus` values, `ReviewActionType`, and `Evidence` are referenced in every subsequent module. Defining them once centrally prevents teammates from independently inventing incompatible representations.

5. **Easy to validate immediately.** `tests/test_models.py` covers type rejection, enum completeness, and defaults — all tests pass before any business logic is written.

**Concrete first commit:** `requirements.txt`, `.env.example`, `backend/models/` (five files), `backend/config.py`, `tests/__init__.py`, and `tests/test_models.py`. That single commit makes the project installable and gives every teammate a stable import target.

---

## Terminal Commands to Verify the Environment Before Implementation

Run these in order after checking out the repository. Each command should exit with code 0 before you begin writing any Phase 1 code.

```bash
# 1. Confirm Python version — 3.10 or later required for | union type hints
python3 --version

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Confirm pip is available inside the venv
pip --version

# 4. Confirm the backend package is discoverable (empty __init__.py already exists)
python3 -c "import backend; print('backend importable')"

# 5. After you populate requirements.txt in Phase 1, install dependencies:
pip install -r requirements.txt

# 6. Confirm pydantic v2 installed correctly
python3 -c "import pydantic; print(pydantic.VERSION)"
# Expected: 2.x.x

# 7. Confirm pydantic-settings installed
python3 -c "import pydantic_settings; print('pydantic_settings ok')"

# 8. Confirm pytest is available
pytest --version

# 9. Run the (initially empty) test suite — should collect 0 tests and exit 0
pytest tests/ -v

# 10. Confirm the data directories exist
ls data/reference data/fixtures 2>/dev/null || echo "data dirs missing — create them"
```

> **Note on Python version:** The models use `X | Y` union syntax (e.g., `str | None`) which requires Python 3.10+. If your team is on 3.9, replace `str | None` with `Optional[str]` from `typing`. Confirm your version in step 1 before writing any code.
