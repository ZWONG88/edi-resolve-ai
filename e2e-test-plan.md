# EDIResolve AI — End-to-End Backend Test Plan

## Overview

Verify that every P0 MVP requirement is exercised by a runnable test and that the
full `pytest` run is green. This plan audits the existing suite, closes coverage gaps,
and adds integration-layer assertions that cross module boundaries in ways the unit
tests currently do not.

**Source of truth:** `docs/PRD.md` · **Release gate:** AT01–AT24 in `tests/test_acceptance.py`
**Current baseline:** 143 passed, 0 failed (2026-10-24, `AI_PROVIDER=mock`)

## Status (2026-10-24): executed

**Result:** 160 passed, 3 xfailed (strict, known bugs), 1 deselected. Coverage `backend/` is 94%, and every module is at 82% or more. `ruff` is clean.
Run it with `.venv/bin/python -m pytest` (`pytest-cov` is installed in `.venv`).

| Task | Outcome |
|---|---|
| 1 Baseline | 143 passed; every module was already at 80% or more (table below) |
| 2 Orchestrator journeys | New `tests/test_e2e_journeys.py` (7 tests) |
| 3 Ingestion | Mostly covered by AT09–AT11, AT21 and AT23. Gaps are in `tests/test_e2e_ingestion.py` (5 tests) |
| 4 Financials | Already covered by AT02, AT12, AT13, AT14 and AT24 through the orchestrator. Nothing added |
| 5 Review workflow | Already covered by AT17 (actions, owner allowlist), AT18 (reason, stale) and AT19 (rollback, idempotency) |
| 6 Audit trail | The cross-case listing and full event order are in the journeys file. The rest is covered by AT17, AT19 and AT22 |
| 7 AI safety | Already covered by `test_ai_resolver.py`, AT06, AT15 and AT16. The journey adds approval after AI_UNAVAILABLE |
| 8 Remittance | Already covered by AT25, AT26, rule 4.2 and `test_rerun_supersedes_820`. The journeys add the on-disk payload and ADJ codes |
| 9 Failures | Already covered by AT20. `tests/test_e2e_edge_cases.py` adds the reference outage |
| 10 Docs | `docs/test_results.md` updated. Datasets 4 and 5 are frozen in `tests/test_datasets.py` |

**Corrections to the API notes in the tasks below** (the original text is kept):
- The parameter is `rerun_case(conn, case_id, corrected_invoice_src=...)`, not `new_invoice_src`.
- `get_cached_explanation()` returns `(AIExplanation, recommendation_version)` or `None`. `recommendation_version` is a string.
- A PO's `DOCUMENT_INGESTED` event has `case_id = NULL`, so a case has exactly one `DOCUMENT_INGESTED`.
- The 820 payload carries `"status": "GENERATED_NOT_TRANSMITTED"`. `"transmitted": false` is in the audit event's `calculations_json`.
- `db.list_findings()` defaults to the current evidence version. Pass `evidence_version` to get history.

**Bugs found:** B1–B6, listed in `docs/test_results.md` under "E2E audit: known issues".

| Module | Coverage | Module | Coverage |
|---|---|---|---|
| orchestrator | 96% | comparator | 94% |
| db | 100% | parser | 88% |
| ai_resolver | 82% | disposition | 100% |
| remittance | 94% | reference | 96% |
| allocation | 100% | receipts | 96% |
| adapters | 92% | money | 100% |
| config | 100% | | |

---

The plan is organized as discrete sub-tasks, each targeted at a specific layer or
scenario cluster. Agent mode implements one sub-task at a time, re-runs `pytest` after
each, and marks the sub-task done before moving on.

---

## How to run

```bash
# Full suite (no keys needed)
pytest

# Only a specific sub-task's file
pytest tests/test_e2e_orchestrator.py -v

# With coverage report
pytest --cov=backend --cov-report=term-missing

# Live watsonx integration (needs .env keys)
pytest -m integration -v
```

---

## Sub-Tasks

---

### Task 1 — Audit Existing Coverage and Confirm Baseline

**Status:** `[ ] pending`

**Intent:** Run the full suite, confirm 143 pass, identify any flaky tests, and produce
a coverage report to see which backend lines are not yet hit. This gives a factual
baseline before writing any new tests.

**Expected Outcomes:**
- `pytest` exits 0 with 143 passed (or more if tests were added).
- `pytest --cov=backend --cov-report=term-missing` produces a per-module coverage table.
- Any gap below 80% on a P0 module is flagged for the follow-on tasks.

**Todo List:**
1. Run `pytest --tb=short -q` and confirm 143 passed, 0 failed.
2. Run `pytest --cov=backend --cov-report=term-missing -q` and record the per-module
   coverage percentages.
3. List every backend module and its coverage %; flag any below 80%:
   `config.py`, `db.py`, `parser.py`, `reference.py`, `comparator.py`, `allocation.py`,
   `adapters.py`, `ai_resolver.py`, `orchestrator.py`, `disposition.py`, `money.py`,
   `remittance.py`, `receipts.py`.
4. Update the coverage table in this plan file before moving to Task 2.

**Relevant Context:**
- `tests/conftest.py` sets `AI_PROVIDER=mock` and `THREE_WAY_ENABLED=false` for all
  tests except `test_settings_*` and `test_three_way.py`.
- The integration test (`test_watsonx_live`) is excluded by default (`-m integration`
  deselection in `pyproject.toml`).

---

### Task 2 — Orchestrator Happy Path E2E (P0 Core Flow)

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_orchestrator.py` that exercises the full `process()`
→ `request_ai_explanation()` → `review_action()` → `list_audit_events()` chain in a
single test, confirming every state transition and audit record end-to-end. This is
the PRD Section 6 user journey in code.

**Expected Outcomes:**
- One test covers the complete AT04 (PRICE_VARIANCE) journey from upload to reviewed.
- Asserts: case created → findings present → AI explanation cached → reviewer approves →
  audit event recorded → `list_cases()` returns updated status.
- A second test covers the AT01 (MATCHED_2WAY) journey confirming no exception is raised
  and review status starts as OPEN (no auto-approve in 2-way mode).

**Todo List:**
1. Create `tests/test_e2e_orchestrator.py` with module-level docstring explaining scope.
2. Add `test_e2e_price_variance_full_journey`:
   - Call `orchestrator.process(conn, inv_AT04, po_AT04, reference_dir=REFERENCE)`.
   - Assert `result_state == "PRICE_VARIANCE"` and `outcome == "CREATED"`.
   - Call `orchestrator.request_ai_explanation(conn, case_id)` → assert explanation
     is not None and `status` is `"OK"` or `"AI_ABSTAIN"` (mock may abstain; both valid).
   - Call `orchestrator.list_cases(conn)` → assert 1 case, status `"PRICE_VARIANCE"`,
     `review_status == "OPEN"`.
   - Call `orchestrator.review_action(conn, ReviewRequest(action=APPROVE, reason="..."))`.
   - Assert case `review_status == "RESOLUTION_APPROVED"`, `version == 2`.
   - Call `orchestrator.list_audit_events(conn, case_id)` → assert events include
     `"RULES_EXECUTED"` and `"HUMAN_APPROVE"`.
3. Add `test_e2e_matched_no_payment_action`:
   - Process AT01 → assert `MATCHED_2WAY`, `review_status == "OPEN"` (no auto-approve
     in 2-way P0 mode).
   - Assert no remittance row exists for this case.
   - Assert audit events do NOT include `"AUTO_APPROVED"` or `"REMITTANCE_820_GENERATED"`.
4. Add `test_e2e_rerun_after_correction`:
   - Process AT04 → get AI explanation (version 1).
   - Call `orchestrator.rerun_case(conn, case_id, new_invoice_src=inv_AT22_corrected)`.
   - Assert new state is `"MATCHED_2WAY"`, `evidence_version == 2`.
   - Assert version-1 AI explanation is still retrievable via
     `orchestrator.get_cached_explanation(conn, case_id, evidence_version=1)`.
   - Assert attempting to approve with `recommendation_version=1` raises `StaleVersionError`.

**Relevant Context:**
- `tests/conftest.py` → `conn` fixture gives an isolated temp SQLite DB.
- `tests/conftest.py` → `fx(name)` resolves fixture file paths.
- `REFERENCE = tests/fixtures/reference/` — pass as `reference_dir=REFERENCE`.
- `orchestrator.review_action` takes a `ReviewRequest` pydantic model.
- `orchestrator.get_cached_explanation` returns `AIExplanation | None`.

---

### Task 3 — Ingestion Contract Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_ingestion.py` covering every document ingestion
scenario: valid, malformed, replay, duplicate conflict, cross-partner isolation, and
the 861 disabled path. These are PRD FR-01, FR-02, and AT09–AT11, AT21, AT23.

**Expected Outcomes:**
- Every `IngestOutcome.outcome` value (`CREATED`, `REPLAY_NOOP`, `QUARANTINED`,
  `DUPLICATE_CONFLICT`) is tested in isolation.
- 861 uploaded with `THREE_WAY_ENABLED=false` → stored as `STORED_UNSUPPORTED`, never
  affects case state.
- Cross-partner invoice → `WAIT_DOCUMENT`, `po_id` is None.
- Malformed envelope (`b"{broken"`) → `QUARANTINED`, error field is `"<envelope>"`.

**Todo List:**
1. Create `tests/test_e2e_ingestion.py`.
2. `test_valid_invoice_creates_case` — process AT01 → assert outcome `"CREATED"`,
   `ingested_documents` has two rows (850 + 810), both `validation_status == "VALID"`.
3. `test_replay_is_idempotent` — process AT09 twice → second outcome `"REPLAY_NOOP"`,
   same `case_id`, exactly 1 allocation row.
4. `test_duplicate_conflict_same_key_different_content` — process AT09 then
   `inv_AT09_conflict.json` → outcome `"DUPLICATE_CONFLICT"`, `ingested_documents` has
   two 810 rows for the same PO, second has `validation_status == "DUPLICATE_CONFLICT"`.
5. `test_malformed_envelope_quarantined` — process `b"{broken"` → outcome `"QUARANTINED"`,
   `result_json["error"]["field"] == "<envelope>"`, no allocation.
6. `test_missing_quantity_quarantined_with_field` — process `inv_AT11_malformed.json` →
   `result_json["error"]["field"] == "lines[0].quantity"`.
7. `test_861_stored_unsupported_when_three_way_disabled` — process AT01 with
   `receipt_src=receipt_AT21.json`, env `THREE_WAY_ENABLED=false` → receipt stored as
   `"STORED_UNSUPPORTED"`, case state `"MATCHED_2WAY"`, findings `receipt` check is
   `"CHECK_NOT_EVALUATED"`.
8. `test_cross_partner_invoice_wait_document` — ingest `po_AT23.json` (SupplierInc),
   then process `inv_AT23_other_partner.json` (OtherSupplierLLC) → `WAIT_DOCUMENT`,
   `po_id is None`.

**Relevant Context:**
- `orchestrator.process(conn, inv_src, po_src, receipt_src=None, reference_dir=...)`.
- `db.list_findings(conn, case_id)` returns all findings for a case.
- `conn.execute("SELECT * FROM ingested_documents")` for raw row assertions.

---

### Task 4 — Financial Arithmetic E2E Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_financials.py` that runs the normalization formula,
extension rounding, and tolerance boundary through the full orchestrator stack (not just
`comparator.py` in isolation). Proves PRD Section 18 arithmetic is correct end-to-end.

**Expected Outcomes:**
- AT13 precision ($0.335 × 3 = $1.01, not $1.00 or $1.005) verified via orchestrator.
- AT14 tolerance boundary ($0.01 passes, $0.0101 fails) verified via orchestrator.
- AT02 UOM normalization (50 CS / 12 EA per CS = 600 EA) verified via orchestrator with
  the correct normalized values in `result_json`.
- Extension mismatch on AT24 detected independently of the blocked price check.
- Currency mismatch (AT12) holds extension check as `NOT_EVALUATED`, not a pass.

**Todo List:**
1. Create `tests/test_e2e_financials.py`.
2. `test_e2e_decimal_precision_at13a` — process AT13a → assert
   `line_results[0]["po_value_normalized"]["extension"] == "1.01"`.
3. `test_e2e_price_basis_normalization_at13b` — process AT13b → assert normalized unit
   price is `"0.10"` (price $10 per 100 EA = $0.10/EA) and extension is `"2.00"`.
4. `test_e2e_uom_normalization_at02` — process AT02 → assert both `po_value_normalized`
   and `invoice_value_normalized` show `quantity == "600"` and `unit_price == "2"`.
5. `test_e2e_tolerance_boundary_at14` — run AT14a and AT14b with a custom policy
   (`price_abs_tolerance=Decimal("0.01")`); assert AT14a passes, AT14b fails, both
   retain the actual delta and policy version in findings.
6. `test_e2e_currency_mismatch_holds_extension` — process AT12 currency fixture →
   finding `"currency"` is `CHECK_NOT_EVALUATED`, finding `"extension_vs_po"` is also
   `CHECK_NOT_EVALUATED`, case state is not `MATCHED_2WAY`.
7. `test_e2e_extension_mismatch_independent_at24` — process AT24 → assert
   `"invoice_total"` finding has `check_status == "CHECK_FAILED"` even though
   `"unit_price"` is `CHECK_NOT_EVALUATED` (missing price basis blocks price check but
   not the independent totals check).

**Relevant Context:**
- `ReconciliationPolicy.baseline().model_copy(update={...})` to set custom tolerance.
- `db.list_findings(conn, case_id)` → keyed by `check_name`.
- AT12 currency fixture: `inv_AT12_currency.json` / `po_AT12.json`.
- AT24: `inv_AT24.json` / `po_AT24.json`.

---

### Task 5 — Human Review Workflow E2E Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_review.py` covering every reviewer action, the
reason requirement, stale version rejection, audit rollback on write failure, and the
idempotency key. These are PRD Section 20.1–20.2 and AT17–AT19.

**Expected Outcomes:**
- Approve, reject, escalate each produce distinct `review_status` values and an audit row.
- Submitting without a reason raises `ValidationError`.
- Submitting with a stale version raises `StaleVersionError`.
- Audit write failure rolls back state and allocations; retry with same idempotency key
  records only one event.
- Escalation requires a valid owner from the allowlist.

**Todo List:**
1. Create `tests/test_e2e_review.py`.
2. `test_approve_records_reason_and_audit` — process AT04, approve with reason →
   assert `review_status == "RESOLUTION_APPROVED"`, `version == 2`, audit event has
   `reviewer_reason` set, `case["disposition"] == "NEXT_STEP_APPROVED"`.
3. `test_reject_keeps_case_open` — process AT04, reject → assert
   `review_status == "RECOMMENDATION_REJECTED"`, case `status` is still `"PRICE_VARIANCE"`.
4. `test_escalate_requires_valid_owner` — process AT03, escalate with
   `owner="product-data owner"` → passes; escalate with `owner="my boss"` → raises
   `ReviewValidationError`; escalate with `owner=None` → raises `ValidationError`.
5. `test_empty_reason_blocked` — process AT04, attempt approve with `reason="   "` →
   raises `ValidationError` matching `"reason"`.
6. `test_stale_version_refused` — process AT04, reject (version becomes 2), attempt
   approve with version=1 → raises `StaleVersionError`, no second audit event.
7. `test_audit_failure_rolls_back_state_and_allocation` — process AT07 (partial billing,
   allocation created), monkeypatch `db.write_audit_event` to raise
   `AuditPersistenceError` on `HUMAN_*` events, attempt approve → raises
   `AuditPersistenceError`, assert `review_status == "OPEN"`, `version == 1`,
   allocation still active. Restore, retry with same idempotency key → succeeds once,
   second retry with same key is a no-op.
8. `test_approval_does_not_change_finding_state` — approve a `PRICE_VARIANCE` case →
   assert case `status` is still `"PRICE_VARIANCE"`, not `"MATCHED_2WAY"`.

**Relevant Context:**
- `ReviewRequest(case_id, case_version, action, reason, escalation_owner, idempotency_key)`.
- `ReviewActionType` values: `APPROVE`, `REJECT`, `ESCALATE`.
- `ESCALATION_OWNERS` in `orchestrator.py` is the valid owner allowlist.
- Idempotency: same key + same action = no-op after first commit.

---

### Task 6 — Audit Trail Completeness Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_audit.py` confirming that every required event type
is written to `audit_events`, that the append-only contract holds, and that all required
fields are present. These are PRD Section 20.2 and AT19.

**Expected Outcomes:**
- After a full AT04 journey, the event log contains at minimum:
  `DOCUMENT_INGESTED` (×2), `RULES_EXECUTED`, `HUMAN_APPROVE`.
- Every event has a non-null `event_at`, `actor`, `case_id`.
- `HUMAN_*` events have `reviewer_reason` populated.
- `ESCALATED` events have `escalation_owner` populated.
- Events are append-only: deleting or modifying a committed event is not possible
  through any public API (no `UPDATE` or `DELETE` paths in `db.py`).
- `list_audit_events(conn)` returns all events across all cases when `case_id=None`.

**Todo List:**
1. Create `tests/test_e2e_audit.py`.
2. `test_required_events_present_after_full_journey` — process AT04, approve → assert
   event types `{"DOCUMENT_INGESTED", "RULES_EXECUTED", "HUMAN_APPROVE"}` are all
   present in `list_audit_events(conn, case_id)`.
3. `test_human_event_has_required_fields` — approve AT04 case → get the `HUMAN_APPROVE`
   event row → assert `reviewer_reason` is not None/empty, `actor` is not None,
   `event_at` is a valid ISO timestamp, `before_state` and `after_state` are set.
4. `test_escalation_event_has_owner` — escalate AT03 case with owner `"pricing owner"`
   → get `HUMAN_ESCALATE` event → assert `escalation_owner == "pricing owner"`.
5. `test_no_delete_path_in_db` — inspect `backend/db.py` source to assert no
   `DELETE FROM audit_events` or `UPDATE audit_events` SQL exists. Use `grep` or a
   string scan of the module source.
6. `test_list_all_events_no_filter` — process two different cases, call
   `list_audit_events(conn)` (no case_id) → assert events from both cases are returned.
7. `test_rerun_adds_version_superseded_event` — process AT04, rerun with corrected
   invoice → assert `"RERUN_VERSION_SUPERSEDED"` event is in history, `before_state`
   is `"PRICE_VARIANCE"`.

**Relevant Context:**
- `db.list_audit_events(conn, case_id=None)` returns all events when case_id omitted.
- Audit events table schema in `backend/db.py` → `init_db()`.
- No public API should issue `DELETE FROM audit_events` — this is a PRD 20.2 constraint.

---

### Task 7 — AI Safety and Isolation Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_ai_safety.py` confirming that the AI layer cannot
mutate case state, inject unsupported facts, or cause the UI to display unvalidated
numbers. These are PRD Section 19 and AT15–AT16.

**Expected Outcomes:**
- Injected fiction (unknown IDs, wrong numbers, disallowed actions) → `AI_ABSTAIN`.
- AI timeout → `AI_UNAVAILABLE`; deterministic findings unchanged.
- `AI_UNAVAILABLE` does not prevent reviewer actions.
- Raw EDI description text is not present in the evidence bundle sent to AI.
- AI explanation is labeled with model version and prompt version.

**Todo List:**
1. Create `tests/test_e2e_ai_safety.py`.
2. `test_injected_unknown_finding_id_abstains` — provide a `generate` function that
   returns `{"status": "OK", "finding_ids": ["F-FAKE"], ...}` → assert `AI_ABSTAIN`.
3. `test_injected_unknown_contract_id_abstains` — `generate` returns a narrative citing
   `"contract.CTR-FAKE-999"` → assert `AI_ABSTAIN`, case state unchanged.
4. `test_injected_wrong_number_abstains` — `generate` returns a narrative containing
   a number not in the evidence bundle (e.g. `"$999.00"`) → assert `AI_ABSTAIN`.
5. `test_injected_disallowed_action_abstains` — `generate` returns
   `"suggested_next_action": "APPROVE_PAYMENT"` → assert `AI_ABSTAIN`.
6. `test_ai_unavailable_leaves_findings_intact` — process AT04, call
   `request_ai_explanation` with a generator that times out → assert
   `status == "AI_UNAVAILABLE"`, `findings["unit_price"]["delta"] == "+0.10"`.
7. `test_ai_unavailable_does_not_block_review` — process AT04, get AI (unavailable),
   then successfully approve the case → assert `review_status == "RESOLUTION_APPROVED"`.
8. `test_description_text_not_in_bundle` — process AT15 fixture (has injection in
   description), call `build_evidence_bundle(result)` → assert product description
   text is not present anywhere in the serialized bundle JSON.
9. `test_explanation_has_model_and_prompt_version` — process AT04, call
   `request_ai_explanation` → assert returned `AIExplanation` has non-empty
   `model_version` and `prompt_version` fields.

**Relevant Context:**
- `orchestrator.request_ai_explanation(conn, case_id, generate=fn, timeout_seconds=N)`.
- `ai_resolver.build_evidence_bundle(result)` is testable in isolation.
- AT15 fixture: `inv_AT15.json` / `po_AT15.json`.
- `ALLOWED_ACTIONS` set in `backend/ai_resolver.py`.

---

### Task 8 — Disposition and Remittance E2E Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_remittance.py` confirming that the EDI 820
remittance file is generated (never transmitted) for qualifying dispositions, that
the dollar amounts are correct, and that re-runs supersede old remittances.

**Expected Outcomes:**
- `AUTO_APPROVED` (3-way clean match with `THREE_WAY_ENABLED=true`) → remittance file
  written to disk, `paid_amount` equals full invoice total.
- `APPROVED_WITH_SHORT_PAY` → remittance written, `adjustment_amount` > 0.
- `HOLD` / `WAIT_RECEIPT` → no remittance generated.
- `NEEDS_HUMAN_AP_REVIEW` → remittance only after a human approves.
- Re-run supersedes the previous remittance (old row becomes `SUPERSEDED`).

**Todo List:**
1. Create `tests/test_e2e_remittance.py`.
2. `test_auto_approved_820_written_to_disk` — process AT25 (3-way clean, with
   `THREE_WAY_ENABLED=true`) → assert `current_remittance` row exists,
   `paid_amount == "200.00"`, `file_path` exists on disk, `status == "GENERATED_NOT_TRANSMITTED"`.
3. `test_short_pay_820_has_adjustment_codes` — process a short-pay case (billed > accepted)
   → assert `current_remittance` row has non-null `payload_json`, and the parsed payload
   contains `"adjustment_amount"` > 0 and at least one `ADJ_*` code in line items.
4. `test_hold_produces_no_820` — process AT26 (WAIT_RECEIPT) → assert
   `current_remittance(conn, case_id) is None`.
5. `test_needs_ap_review_no_820_until_approved` — process AT27 (RECEIPT_VARIANCE,
   `NEEDS_HUMAN_AP_REVIEW`) → assert no remittance before review; approve case →
   assert remittance generated after approval.
6. `test_rerun_supersedes_remittance` — process AT25, get remittance v1; rerun case with
   same invoice → assert old remittance row has `status == "SUPERSEDED"`, new row has
   `status == "GENERATED_NOT_TRANSMITTED"`.
7. `test_820_never_transmitted_flag` — for any generated remittance, assert the
   `payload_json` contains `"transmitted": false` (or equivalent) and no HTTP call
   is made (no network mock needed; just assert the field).

**Relevant Context:**
- `orchestrator.current_remittance(conn, case_id)` → latest non-superseded row.
- `remittance.can_generate(result)` — only AUTO_APPROVED / SHORT_PAY auto; NEEDS_HUMAN
  requires `human_approved=True`.
- Three-way tests need `monkeypatch.setenv("THREE_WAY_ENABLED", "true")`.
- AT25 uses `tests/team_docs.py` helpers: `t.po(...)`, `t.invoice(...)`, `t.receipt(...)`.

---

### Task 9 — Safe Failure and Retry Tests

**Status:** `[ ] pending`

**Intent:** Create `tests/test_e2e_failures.py` confirming TECHNICAL_HOLD on rules
failure, bounded retry, no double allocations after restart, and idempotent rerun.
These are PRD Section 20.3 and AT20.

**Expected Outcomes:**
- Comparator failure → `TECHNICAL_HOLD`, retry count equals `max_retry_attempts`.
- No allocation written during a technical hold.
- After restart (new `init_db` call), `rerun_case` succeeds and produces correct state.
- Calling `rerun_case` twice on a clean case is idempotent (no double allocation).

**Todo List:**
1. Create `tests/test_e2e_failures.py`.
2. `test_comparator_failure_technical_hold` — monkeypatch `comparator.compare` to raise
   `RuntimeError`, process AT07 → assert `result_state == "TECHNICAL_HOLD"`, retry audit
   events count equals `get_settings().max_retry_attempts`, no allocations.
3. `test_restart_and_rerun_recovers` — same setup; restore comparator; close conn;
   re-open DB with `db.init_db(db_file)`; call `orchestrator.rerun_case` → assert
   `"PARTIAL_BILLING"`, exactly one active allocation of `"40"`.
4. `test_rerun_twice_idempotent` — run the recovery from step 3 a second time → assert
   state is still `"PARTIAL_BILLING"`, still exactly one allocation, no duplicate events.
5. `test_reference_failure_insufficient_evidence` — monkeypatch
   `orchestrator.reference.load_uom_conversions` to raise `FileNotFoundError`, process
   AT02 → assert `INSUFFICIENT_EVIDENCE` or `TECHNICAL_HOLD` (whichever the code
   produces) and no float arithmetic panic.

**Relevant Context:**
- `monkeypatch.setattr(orchestrator.comparator, "compare", broken_fn)`.
- `db.init_db(db_file)` is idempotent — safe to call after `conn.close()`.
- `orchestrator.rerun_case(conn, case_id, reference_dir=REFERENCE)` returns the new state string.

---

### Task 10 — Validate and Update docs/test_results.md

**Status:** `[ ] pending`

**Intent:** Run the complete suite after all new tests are added, confirm ≥ 143 + new
tests pass, update `docs/test_results.md` with the new count and coverage numbers, and
commit the changes.

**Expected Outcomes:**
- `pytest` exits 0 with all tests passing.
- `pytest --cov=backend --cov-report=term-missing` shows ≥ 80% on every P0 module.
- `ruff check .` passes with no new warnings.
- `docs/test_results.md` updated with new totals and date.

**Todo List:**
1. Run `pytest --tb=short -q` and confirm 0 failures.
2. Run `pytest --cov=backend --cov-report=term-missing -q` and record final coverage.
3. Run `ruff check .` and fix any new lint issues introduced by the new test files.
4. Update the Summary table in `docs/test_results.md`:
   - New total test count.
   - Coverage percentages per module.
   - Date of run.
5. Commit: `git add tests/test_e2e_*.py docs/test_results.md && git commit -m "test: comprehensive e2e backend test suite"`.
6. Push to `feature/three-way-rules`.

**Relevant Context:**
- `pyproject.toml` configures ruff and pytest (check before adding new `# noqa` suppressions).
- `docs/test_results.md` format is already established — match the existing table style.

---

## Coverage Targets (P0 Modules)

| Module | Minimum Target | Notes |
|---|---|---|
| `orchestrator.py` | 85% | Core entry point; all public functions called |
| `comparator.py` | 90% | Every check branch exercised |
| `db.py` | 85% | All read/write functions, transaction rollback |
| `parser.py` | 90% | Valid, malformed, replay, hash stability |
| `ai_resolver.py` | 85% | Mock, unavailable, validation rejection paths |
| `disposition.py` | 90% | All adjustment code paths |
| `remittance.py` | 80% | Generate, supersede, never-transmit |
| `reference.py` | 85% | Missing, expired, overlapping |
| `allocation.py` | 85% | Cumulative over-billing, no double-count |
| `receipts.py` | 80% | Incremental, cumulative, ledger balance |
| `adapters.py` | 85% | Team JSON → models, array splitting |
| `money.py` | 95% | Decimal rounding, no float drift |
| `config.py` | 80% | Settings defaults, env override |

---

## Key Constraints (apply to all new tests)

- All new tests use the `conn` and `run` fixtures from `conftest.py` — no new DB setup.
- No `float` in any assertion — use `Decimal` or string comparisons.
- Every new test function has a docstring explaining which PRD requirement it covers.
- New test files must not import from `pages/` — backend only.
- `AI_PROVIDER=mock` is enforced by `conftest.py` — do not override unless testing AI explicitly.
- Three-way tests that need `THREE_WAY_ENABLED=true` use `monkeypatch.setenv`.
