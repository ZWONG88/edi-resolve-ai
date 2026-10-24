# Test Results — P0 Release Gate

Recorded 2026-10-23 on branch `feature/three-way-rules`, synthetic fixtures plus the team datasets (`tests/fixtures/`), Python 3.12.7, `AI_PROVIDER=mock`.
Updated 2026-10-24: backend e2e audit added 20 tests (`tests/test_e2e_*.py`, datasets 4 and 5); see "E2E audit" below.
Updated 2026-10-24: live IBM watsonx.ai integration verified (`AI_PROVIDER=watsonx`, model `ibm/granite-4-h-small`, project `8b6b4870-b6dc-4834-ab3f-476707d3b432`, US South).
These are results on labeled synthetic data only. They say nothing about production
accuracy or payment timing (PRD 22.2).

## Summary

| Suite | Result |
|---|---|
| Full `pytest` | **168 passed**, 0 failed, 1 deselected (live watsonx integration test) |
| E2E journeys / ingestion / edge cases (`tests/test_e2e_*.py`) | **21 passed** (bugs B1–B6 fixed) |
| `pytest --cov=backend` | **94%** overall; lowest are ai_resolver 82% (live watsonx path), parser 88% |
| AT01–AT24 two-way gate (`tests/test_acceptance.py`) | **24 / 24 passed** |
| AT25–AT35 three-way (`tests/test_three_way.py`) | **AT25–AT32, AT34, AT35 passed**. AT33 and AT36 are not implemented. |
| Team rules doc (`tests/test_three_way.py`, `test_disposition.py`) | Rules 1.2, 2.2, 3.1, 3.2, 4.1 and 4.2, the 820 and supersession all pass |
| Team datasets (`tests/test_datasets.py`) | Datasets 1–5 outcomes frozen and passing; re-import is idempotent |
| PR #1 schema tests (`tests/test_models.py`) | **23 / 23 passed** |
| Other: parser 13, reference 11, comparator 13, AI 16, adapters 7, db 2, UI 3 | all passed |
| `ruff check .` | All checks passed |
| **Live watsonx.ai integration** (`pytest -m integration`) | **PASSED** — `ibm/granite-4-h-small`, 2026-10-24 |

## Team datasets: per-category outcomes (three-way)

| Dataset | Invoices | Outcome |
|---|---|---|
| Dataset 1 (PO-10001, 50 lines) | 1 | `MATCHED_3WAY` → `AUTO_APPROVED`; $36,673.98 approved, $0 blocked; 820 paid in full |
| DATASET 3 (PO-10002, 50 lines) | 1 | `NEEDS_HUMAN_AP_REVIEW`; 7 lines flagged; $382.00 blocked |
| Dataset2 | 60 | 30 auto-approved, 10 short pay, 10 need AP review, 10 held |
| DATASET 4 (PO-10003, 50 lines) | 1 | `RECEIPT_REVIEW` → `NEEDS_HUMAN_AP_REVIEW`; $35,582.48 approved, $316.16 blocked |
| Dataset5 (PO-10004, 50 lines) | 1 | `REVIEW_LINK` → `HOLD`; $29,138.16 held |

**Dataset2 by category**

| Category | Count | State → disposition |
|---|---|---|
| Clean | 10 | `MATCHED_3WAY` → `AUTO_APPROVED` |
| CS vs EA (pack size from catalog) | 10 | `MATCHED_3WAY` → `AUTO_APPROVED` |
| Partial billing | 5 | `PARTIAL_BILLING` → `AUTO_APPROVED` |
| Price overcharge | 5 | `PRICE_VARIANCE` → `APPROVED_WITH_SHORT_PAY` (blocked = (inv − PO) × qty) |
| Header total $1.00 above lines | 5 | `PRICE_VARIANCE` → `AUTO_APPROVED` ($1.00 waived per rule 4.1, team decision; variance still recorded) |
| Billed > accepted | 5 | `RECEIPT_VARIANCE` → `APPROVED_WITH_SHORT_PAY` |
| Billed > ordered and over-received | 5 | `OVER_RECEIPT` → `NEEDS_HUMAN_AP_REVIEW` |
| Damaged packaging | 5 | `RECEIPT_REVIEW` → `NEEDS_HUMAN_AP_REVIEW` |
| Currency mismatch | 5 | `INSUFFICIENT_EVIDENCE` → `HOLD` |
| No 861 receipt | 5 | `WAIT_RECEIPT` → `HOLD` |

Dataset2 totals: invoiced $23,614.20, approved $19,106.30, blocked $524.80, held $3,983.10.

**DATASET 3 flagged lines**

| Line | What was found | Blocked |
|---|---|---|
| 7 | Billed 103, ordered fewer, accepted 83 | `ADJ_SHORTAGE` $162.60 |
| 8 | Billed 92, accepted 82 | `ADJ_SHORTAGE` $84.20 |
| 12 | Invoice $11.08 vs PO $9.58 | `ADJ_PRICE` $70.50 |
| 18 | Ghost line SKU2018X, not on the PO | `ADJ_UNMAPPED` $226.40 |
| 20 | Rejected goods, accepted 32 of 38 | `ADJ_SHORTAGE` $71.40, receiving review |
| 25 | Line total $1,138.05 ≠ 83 × $13.35 | `ADJ_EXTENSION` $30.00 |
| 42 | Accepted more than ordered | review only |

The invoice header total is also $263.10 below the sum of its lines. The approved amount is
capped at the header total, so blocked = $37,224.40 − $36,842.40 = $382.00.

## AT01–AT24 (actual outcomes)

| Test | Result |
|---|---|
| `test_at01_exact_match` | PASSED |
| `test_at02_uom_normalized_equality` | PASSED |
| `test_at03_missing_conversion_insufficient_evidence` | PASSED |
| `test_at04_price_variance` | PASSED |
| `test_at05_contract_displayed_separately` | PASSED |
| `test_at06_ai_abstains_without_contract` | PASSED |
| `test_at07_partial_billing` | PASSED |
| `test_at08_cumulative_over_authorization` | PASSED |
| `test_at09_replay_and_conflict` | PASSED |
| `test_at10_missing_po_then_ambiguous_link` | PASSED |
| `test_at11_quarantine_with_exact_error` | PASSED |
| `test_at12_currency_and_unsupported_charges` | PASSED |
| `test_at13_decimal_precision` | PASSED |
| `test_at14_tolerance_boundary` | PASSED |
| `test_at15_ai_cannot_inject_facts` | PASSED |
| `test_at16_ai_unavailable_keeps_findings` | PASSED |
| `test_at17_three_review_actions` | PASSED |
| `test_at18_blocked_closure_reason_and_stale` | PASSED |
| `test_at19_audit_failure_rolls_back_then_retry` | PASSED |
| `test_at20_rules_failure_hold_retry_restart` | PASSED |
| `test_at21_receipt_with_three_way_disabled` | PASSED |
| `test_at22_rerun_invalidates_stale_suggestion` | PASSED |
| `test_at23_no_cross_partner_link` | PASSED |
| `test_at24_missing_basis_totals_and_contract_conflict` | PASSED |

AT23 (cross-tenant/partner isolation) is enforced by scoping PO lookup to
`(buyer, supplier, po_number)`. It is a single-tenant prototype, so there is no real
tenant isolation; the test covers the cross-partner case only.

## Per-category detection (PRD 22.2)

Each scenario runs end to end through `backend.orchestrator.process` on a fresh database.
Results are reported per category so that easy matches can't hide misses.

| Category | Fixture | Expected | Actual | |
|---|---|---|---|---|
| Exact match | `inv_AT01` | MATCHED_2WAY | MATCHED_2WAY | ✅ |
| UOM normalized match | `inv_AT02` | MATCHED_2WAY | MATCHED_2WAY | ✅ |
| Rounding precision | `inv_AT13a` | MATCHED_2WAY | MATCHED_2WAY | ✅ |
| Price basis per 100 | `inv_AT13b` | MATCHED_2WAY | MATCHED_2WAY | ✅ |
| Price variance | `inv_AT04` | PRICE_VARIANCE | PRICE_VARIANCE | ✅ |
| Price variance + contract | `inv_AT05` | PRICE_VARIANCE | PRICE_VARIANCE | ✅ |
| Price variance + injected text | `inv_AT15` | PRICE_VARIANCE | PRICE_VARIANCE | ✅ |
| Partial billing | `inv_AT07` | PARTIAL_BILLING | PARTIAL_BILLING | ✅ |
| Missing UOM conversion | `inv_AT03` | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | ✅ |
| Currency mismatch | `inv_AT12_currency` | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | ✅ |
| Missing price basis | `inv_AT24` | INSUFFICIENT_EVIDENCE | INSUFFICIENT_EVIDENCE | ✅ |
| Unsupported freight | `inv_AT12_freight` | NOT_EVALUATED | NOT_EVALUATED | ✅ |
| Missing PO | `inv_AT10_missing_po` | WAIT_DOCUMENT | WAIT_DOCUMENT | ✅ |
| Cross-partner PO | `inv_AT23_other_partner` | WAIT_DOCUMENT | WAIT_DOCUMENT | ✅ |
| Repeated SKU | `inv_AT10_repeated` | REVIEW_LINK | REVIEW_LINK | ✅ |
| Missing quantity | `inv_AT11_malformed` | QUARANTINED | QUARANTINED | ✅ |
| Unsupported version | `inv_AT11_unsupported` | QUARANTINED | QUARANTINED | ✅ |
| Cumulative over-billing | `inv_AT08b (after AT08a)` | QUANTITY_VARIANCE | QUANTITY_VARIANCE | ✅ |
| Exact replay | `inv_AT09 x2` | REPLAY_NOOP | REPLAY_NOOP | ✅ |
| Same ID, different content | `inv_AT09_conflict` | DUPLICATE_CONFLICT | DUPLICATE_CONFLICT | ✅ |
**20 / 20 scenarios** produced the expected deterministic result.

## Not verified here

- **Live IBM watsonx.ai call.** `tests/test_ai_resolver.py::test_watsonx_live` is marked
  `integration` and needs `AI_API_KEY` / `WATSONX_PROJECT_ID` in `.env`.
  Run it with `pytest -m integration`.
- **Manual browser walkthrough.** UI flows were checked with Streamlit's headless `AppTest`
  (`tests/test_ui.py`) and a server boot/health check, not by clicking through in a browser.

## Definition of Done (PRD section 13)

| Item | Status |
|---|---|
| PO + invoice load | ✅ parser + Upload page (samples or file upload) |
| Correct detection of price / quantity / UOM / partial / missing evidence | ✅ table above |
| Evidence-backed explanation | ✅ stub + validated watsonx path; every claim cites bundle IDs |
| Decision recording (approve / reject / escalate with reason) | ✅ AT17–AT19 |
| Audit history | ✅ Audit History page, append-only `audit_events` |
| At least one insufficient-evidence case | ✅ AT03, AT12, AT24 |
| Documented limitations | ✅ README "Known limitations" |
| Reliable demo | ✅ offline mock by default; no keys needed; `mock_unavailable` demos AI failure |

## DATASET 4 and Dataset5 flagged lines

| Dataset | Line | What was found | Blocked |
|---|---|---|---|
| 4 | 5 | Billed 70, accepted 58 | `ADJ_SHORTAGE` $114.00 |
| 4 | 15 | Billed 20 of 28 ordered | none (partial billing) |
| 4 | 17 | Rejected 4, accepted 46 of 50 billed | `ADJ_SHORTAGE` $48.56, receiving review |
| 4 | 37 | Line total $712.68 ≠ 42 × $16.54 | `ADJ_EXTENSION` $18.00 |
| 4 | 45 | Accepted 61, ordered 54 | review only |
| 4 | header | Invoice total $135.60 above the sum of lines | $135.60 |
| 5 | 4 | Invoice $8.68 vs PO $7.18 | `ADJ_PRICE` $69.00 |
| 5 | 8 | Billed 12, accepted 3 | `ADJ_SHORTAGE` $73.98 |
| 5 | 12 | Rejected 5, accepted 43 of 48 | `ADJ_SHORTAGE` $46.30, receiving review |
| 5 | 19 | Not received | `ADJ_SHORTAGE` $454.28, receiving review |
| 5 | 22 | Billed 74, ordered/accepted 68 | `ADJ_SHORTAGE` $71.16 |
| 5 | 25 | Accepted 33, ordered 25 | review only |
| 5 | 31 | Receipt SKU4013 vs PO SKU4031 | `REVIEW_LINK`: receipt not linked |
| 5 | 36 | Line total $862.00 ≠ 54 × $15.50 | `ADJ_EXTENSION` $25.00 |
| 5 | 40 | Receipt in CS, no CS→EA conversion | `INSUFFICIENT_EVIDENCE` |
| 5 | header | $45.00 shipping | totals `NOT_EVALUATED` → `HOLD` (rules doc) |

## E2E audit: issues found and fixed (2026-10-24)

| # | Issue | Fix | Test |
|---|---|---|---|
| B1 | Rejecting an `AUTO_APPROVED` case left its 820 live | REJECT/ESCALATE voids the live 820 (`REMITTANCE_820_VOIDED` event); a later APPROVE regenerates it | `test_reject_voids_820_and_reapproval_restores_it`, `test_escalate_voids_820` |
| B2 | `rerun_case` on a quarantined case raised `ParseError` | Raises `ReviewValidationError`; the Review page shows it | `test_rerun_quarantined_raises_review_validation_error` |
| B3 | Rerunning a `DUPLICATE_CONFLICT` case lifted the hold | Refused with `ReviewValidationError`; the conflict stays held | `test_rerun_duplicate_conflict_refused` |
| B4 | Demo reset deleted every case and audit event (PRD 20.2) | `reset_test_data` deletes only `sandbox` data (sample scenarios, dataset-folder imports); uploaded cases, their history and any sandbox doc a real case uses are kept; the reset is audited (`TEST_DATA_RESET`) | `test_reset_deletes_only_test_data`, `test_reset_clears_sample_cases`, `test_init_db_adds_sandbox_column_to_old_databases` |
| B5 | `summarize()` totals not formatted to cents | Totals use `fmt_money` | `test_summarize_totals_two_decimals` |
| B6 | Reused idempotency key with a different action was a silent no-op | Raises `ReviewValidationError` unless the case and action match | `test_idempotency_key_reused_for_other_action_refused` |
