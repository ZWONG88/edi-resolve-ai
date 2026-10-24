# EDIResolve AI

AI-assisted discrepancy detection and resolution for EDI purchase orders (850) and invoices (810).
**SpartHill Hacks 2026 · IBM AI Challenge**

Retailers and suppliers lose time and money when invoices don't match purchase orders: wrong
prices, case-vs-each unit mix-ups, over-billing across several invoices, malformed files.
EDIResolve AI reconciles each invoice against its PO with a **deterministic rules engine**, then
uses **IBM Granite on watsonx.ai** to explain the findings for a human reviewer. Every
explanation cites the evidence it relies on. The AI never does the math and never makes the
decision.

## Quickstart (no keys needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app runs fully offline with a rules-based AI stub. Open **Upload & Process**, pick a
sample scenario (for example *AT04 · Price variance*), click **Process**, then open
**Exception Review**.

Run the tests:

```bash
pytest                 # unit + AT01–AT24 acceptance gate + UI smoke tests
pytest -m integration  # live watsonx.ai call (needs .env keys)
ruff check .
```

## Using IBM watsonx.ai (optional)

1. `cp .env.example .env`
2. Fill in:
   - `WATSONX_API_KEY`: from cloud.ibm.com, then **Manage → Access (IAM) → API keys**
   - `WATSONX_PROJECT_ID`: from dataplatform.cloud.ibm.com/wx, then your project, **Manage → General**
   - `WATSONX_URL`: your region, e.g. `https://us-south.ml.cloud.ibm.com`
   - `WATSONX_MODEL_ID`: a Granite instruct model available in your region (default
     `ibm/granite-3-8b-instruct`)
3. Set `AI_STUB_MODE=false` and restart the app.

AI runs **on demand** (the *Get AI suggestion* button on an open exception) and is
**cached per case evidence version**. A full demo uses only a few dozen calls, which fits
comfortably in the watsonx.ai Lite plan.

## How it works

```
850 PO ─┐                                         ┌─> Exception Review (human decides)
        ├─> parser ─> comparator (rules) ─> case ─┤
810 INV ┘   SHA-256,   Decimal, UOM +      + audit └─> AI explanation (Granite, validated)
            raw fields  price-basis normalization
```

| Module | Role |
|---|---|
| `backend/parser.py` | Validates 850/810 JSON and keeps the original strings. Every number is a `Decimal` and JSON floats are rejected. Each document gets a SHA-256 provenance hash. |
| `backend/reference.py` | The only reader of `data/reference/` (UOM conversions, price contracts). Missing, expired or overlapping records are surfaced, never guessed. |
| `backend/comparator.py` | Pure rules engine. `Q_base = Q × f`, `P_base = P / (b × f)`, `ROUND_HALF_UP` only at line extension, configurable tolerances (zero by default). |
| `backend/allocation.py` | Cumulative invoice-vs-PO allocation, so over-billing across invoices is caught. |
| `backend/ai_resolver.py` | Builds the evidence bundle (no raw EDI, no free text). Calls Granite and **validates** the output: unknown citations, numbers that disagree with the rules, invented IDs or disallowed actions all mean the narrative is discarded. |
| `backend/service.py` | Workflow: ingest, dedupe/replay, reconcile with bounded retry, review actions, rerun on corrected evidence. |
| `backend/db.py` | SQLite. Each state change and its audit event are committed in a single transaction. |
| `pages/` | Streamlit pages: Upload & Process, Exception Review, Audit History. |

### Result states

`MATCHED_2WAY` · `PRICE_VARIANCE` · `QUANTITY_VARIANCE` · `PARTIAL_BILLING` ·
`INSUFFICIENT_EVIDENCE` · `WAIT_DOCUMENT` · `REVIEW_LINK` · `QUARANTINED` ·
`DUPLICATE_CONFLICT` · `TECHNICAL_HOLD` · `NOT_EVALUATED` · `AI_ABSTAIN` · `AI_UNAVAILABLE`

A check that was **not evaluated** is never treated as a pass or a failure.

## Supported scenarios

| Scenario | Fixture | Result |
|---|---|---|
| Exact match | AT01 | `MATCHED_2WAY` (receipt check `NOT_EVALUATED`) |
| 50 CS vs 600 EA with verified 12 EA/CS | AT02 | `MATCHED_2WAY` after normalization |
| Same, but no conversion on file | AT03 | `INSUFFICIENT_EVIDENCE`; dependent checks not evaluated |
| $2.00 vs $2.10 | AT04–AT06 | `PRICE_VARIANCE` +$0.10/EA, +$10.00; AI cites a contract only if one exists, otherwise abstains |
| 40 of 100 invoiced | AT07 | `PARTIAL_BILLING`, 60 EA balance open |
| 70 then 40 of 100 | AT08 | `QUANTITY_VARIANCE`, cumulative 110 |
| Re-upload / same ID, different content | AT09 | replay no-op / `DUPLICATE_CONFLICT` |
| Missing PO / repeated SKU | AT10 | `WAIT_DOCUMENT` / `REVIEW_LINK` |
| Missing quantity / unsupported version | AT11 | `QUARANTINED` with the exact field |
| EUR vs USD / freight charge | AT12 | held; totals `NOT_EVALUATED` |
| $0.335 × 3; $10 per 100 EA | AT13 | $1.01; $0.10/EA → $2.00 |
| Prompt injection in description | AT15 | description never reaches the AI; forged output rejected |
| Missing price basis + bad total + overlapping contracts | AT24 | price blocked, totals variance kept, contract conflict flagged |

The full results are in [`docs/test_results.md`](docs/test_results.md). Scope and acceptance
criteria are in [`docs/PRD.md`](docs/PRD.md).

## Safety rules

- All money is `decimal.Decimal`. There is no float arithmetic, and rounding happens only at line extension.
- The AI receives structured findings only, never raw EDI or description text. It performs no arithmetic and every claim must cite evidence.
- AI output is labeled **"AI Suggestion — requires human review"**.
- Every approve, reject or escalate needs a reason. Escalation needs an accountable owner. Stale actions are refused.
- If an audit write fails, the action is rolled back and the reviewer sees **"Action not saved"**.
- Approval never turns a case into `MATCHED`. Nothing here pays an invoice or changes an external system.

## Known limitations

- **Synthetic data only.** Fixtures are hand-labeled JSON that stands in for EDI 850/810. Real X12 parsing is out of scope.
- **Two-way only.** P2 three-way receipt (861) matching is disabled (`THREE_WAY_ENABLED=false`). 861 files are stored and labeled as unsupported.
- **Single tenant.** Partner isolation comes from scoping PO lookup to buyer + supplier + PO number. There is no authentication or role-based access.
- **Header charges.** Freight, tax and allowances aren't modeled. Their presence holds the totals check as `NOT_EVALUATED`.
- **watsonx.ai Lite plan.** It allows 300k tokens per month, and services are deleted after 30 days of inactivity.
- **SQLite.** The database is local and single-process. It isn't tamper-resistant audit storage.

## Team

Data Engineering and Parsing · AI Root-Cause Analysis · Software Engineering ·
Systems Integration and Deployment · Business Process Design · Product Testing and Presentation
