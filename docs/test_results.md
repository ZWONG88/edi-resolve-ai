# Test Results — P0 Release Gate

Recorded 2026-10-23 on branch `feature/p0-mvp` (PR #1 schemas merged), synthetic fixtures (`tests/fixtures/`), Python 3.12.7, `AI_PROVIDER=mock`.
These are results on labeled synthetic data only. They say nothing about production
accuracy or payment timing (PRD 22.2).

## Summary

| Suite | Result |
|---|---|
| Full `pytest` | **104 passed**, 0 failed, 1 deselected (live watsonx integration test) |
| AT01–AT24 acceptance gate (`tests/test_acceptance.py`) | **24 / 24 passed** |
| PR #1 schema tests (`tests/test_models.py`) | **23 / 23 passed** |
| Other unit tests: parser 13, reference 11, comparator 13, AI 16, db 2, UI smoke 2 | all passed |
| `ruff check .` | All checks passed |
| `float(` in `backend/` money paths | none (only the AI timeout in seconds) |

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
