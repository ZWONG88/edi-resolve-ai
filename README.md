# EDIResolve AI

AI-assisted three-way reconciliation of EDI purchase orders (850), receiving advice (861) and
invoices (810).
**SpartHill Hacks 2026 · IBM AI Challenge**

Retailers and suppliers lose time and money when invoices don't match purchase orders: wrong
prices, case-vs-each unit mix-ups, over-billing across several invoices, malformed files.
EDIResolve AI reconciles each invoice against its PO **and the goods actually accepted at the
dock** with a **deterministic rules engine** that follows the team's
[3-way reconciliation rules](docs/rules-comparison.md):

- **Clean invoices are auto-approved.**
- **Discrepancies are highlighted** with a suggested correction and the dollar amount
  blocked. Discrepancies include price overcharges, short shipments, ghost lines, unit
  mix-ups and math errors.
- **A corrected EDI 820 remittance file is generated.** It is never transmitted.

**IBM Granite on watsonx.ai** explains the findings for a human reviewer, citing evidence. The
AI never does the math and never makes the decision.

## Quickstart (no keys needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The app runs fully offline with a rules-based AI mock (`AI_PROVIDER=mock`). Open
**Upload & Process**, choose **Import dataset folder**, pick a dataset and click
**Import & reconcile**:

- **Dataset 1:** a clean 50-line PO. It auto-approves.
- **Dataset2:** 60 invoices that cover every rule.
- **DATASET 3:** a 50-line PO with seeded discrepancies.
- **DATASET 4:** a 50-line PO with shortages, rejected goods, an extension error, a header
  overbill and an over-receipt. It needs AP review, with $316.16 blocked.
- **Dataset5:** a 50-line PO with a swapped receipt SKU, a missing CS→EA conversion and a
  $45 shipping charge. It is held, with $29,138.16 not evaluated.

Then open **Exception Review** to see the highlighted lines, suggested corrections and the
820 download.

### Adding your own data

Put a folder of team-format EDI JSON files under `data/raw/<name>/`. A file may hold one
document or an array of documents. Supported types:

- `transaction_type` `"850"` (purchase order)
- `"861"` (receiving advice), with `received_quantity` / `accepted_quantity` / `rejected_quantity`
- `"810"` (invoice), with `line_total` and `invoice_total`

Lines are matched by `line_number` + `buyer_sku`. Pack sizes for case/each conversions go in
`data/reference/uom_conversions.json`. The folder then appears in the import picker.

Run the tests:

```bash
pytest                 # unit, AT01–AT35 acceptance, team datasets, UI smoke tests
pytest -m integration  # live watsonx.ai call (needs .env keys)
ruff check .
```

## Using IBM watsonx.ai (optional)

1. `cp .env.example .env`
2. Fill in:
   - `AI_API_KEY`: from cloud.ibm.com, then **Manage → Access (IAM) → API keys**
   - `WATSONX_PROJECT_ID`: from dataplatform.cloud.ibm.com/wx, then your project, **Manage → General**
   - `WATSONX_URL`: your region, e.g. `https://us-south.ml.cloud.ibm.com`
   - `AI_MODEL_ID`: a Granite instruct model available in your region (default
     `ibm/granite-3-8b-instruct`)
3. Set `AI_PROVIDER=watsonx` and restart the app.

`AI_PROVIDER=mock_unavailable` makes every AI call fail safely. Use it to demo that
deterministic findings and reviewer actions keep working when the AI is down (AT16).
All settings live in `backend/config.py`. See `.env.example` for the full list.

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
| `backend/models/` | Shared Pydantic v2 contract (from PR #1): `PurchaseOrder`, `Invoice`, `Money`, `Evidence`, `CaseResult`, `ReviewRequest`, ... |
| `backend/config.py` | `Settings` (pydantic-settings): every environment variable in one place. |
| `backend/parser.py` | Validates 850/810 JSON into the Pydantic models and keeps the original strings. Every number is a `Decimal` and JSON floats are rejected. Each document gets a `sha256:` provenance hash. |
| `backend/reference.py` | The only reader of `data/reference/` (UOM conversions, price contracts). Missing, expired or overlapping records are surfaced, never guessed. |
| `backend/comparator.py` | Pure rules engine. `Q_base = Q × f`, `P_base = P / (b × f)`, `ROUND_HALF_UP` only at line extension, configurable tolerances (zero by default). |
| `backend/allocation.py` | Cumulative invoice-vs-PO allocation, so over-billing across invoices is caught. |
| `backend/ai_resolver.py` | Builds the evidence bundle (no raw EDI, no free text). Calls Granite and **validates** the output: unknown citations, numbers that disagree with the rules, invented IDs or disallowed actions all mean the narrative is discarded. |
| `backend/orchestrator.py` | The only backend module the UI imports. Covers ingest, dedupe/replay, reconcile with bounded retry, review actions (`ReviewRequest`) and rerun on corrected evidence. |
| `backend/db.py` | SQLite. Each state change and its audit event are committed in a single transaction. |
| `pages/` | Streamlit pages: Upload & Process, Exception Review, Audit History. |

### Parser output contract (for the X12 parser)

Any upstream parser, such as a real X12 850/810 reader, should produce the models in
`backend.models`:

```python
from backend.models import PurchaseOrder, POLine, Invoice, InvoiceLine, Money
```

- Quantities are `Decimal`. Prices are `Money(amount=Decimal("0.335"))` at full precision.
  Never use `Money.from_str()` for unit prices, because it rounds to cents.
- `price_basis` is `None` when the source doesn't state it. Never default it to `1`.
- `Invoice` must carry `buyer_id` and `supplier_id`, because PO lookup is scoped to the trading partner.
- `InvoiceLine.line_reference` is optional. Without it, lines link by product ID, then UPC.
  If more than one PO line matches, the case goes to `REVIEW_LINK`.

### Result states

`MATCHED_3WAY` · `MATCHED_2WAY` · `PRICE_VARIANCE` · `QUANTITY_VARIANCE` · `RECEIPT_VARIANCE` ·
`RECEIPT_REVIEW` · `OVER_RECEIPT` · `UNMAPPED_LINE_ITEM` · `PARTIAL_BILLING` · `WAIT_RECEIPT` ·
`INSUFFICIENT_EVIDENCE` · `WAIT_DOCUMENT` · `REVIEW_LINK` · `QUARANTINED` · `DUPLICATE_CONFLICT` ·
`TECHNICAL_HOLD` · `NOT_EVALUATED` · `AI_ABSTAIN` · `AI_UNAVAILABLE`

A check that was **not evaluated** is never treated as a pass or a failure.

### Invoice dispositions (rules doc Stage 5)

| Disposition | When | Payment |
|---|---|---|
| `AUTO_APPROVED` | Three-way clean (variances ≤ $1.00 waived) | Full amount; 820 generated |
| `APPROVED_WITH_SHORT_PAY` | Blocked cash between $0.01 and $500 | Approved amount; 820 with `ADJ_*` codes |
| `NEEDS_HUMAN_AP_REVIEW` | Blocked > $500, damaged/rejected goods, over-receipt | 820 only after an AP reviewer approves |
| `HOLD` / `PO_HEADER_MISMATCH` / `SCHEMA_VALIDATION_ERROR` / `DUPLICATE_INVOICE_REJECTED` | Missing receipt or evidence, currency mismatch, no PO, bad file, conflicting duplicate | Nothing approved |

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
- Approval never turns a case into `MATCHED`. Auto-approval happens only on a full three-way match.
- EDI 820 files are **generated, never transmitted**. Nothing here moves money or changes an external system.
- Billing is capped at the **accepted** receipt quantity. Damaged or rejected goods go to receiving review.

## Known limitations

- **JSON, not raw X12.** The team datasets are JSON renderings of 850/810/861. Raw X12 segment parsing is out of scope.
- **Derived pack sizes.** The case/each factors for the Dataset2 SKUs were derived from the data and still need verification.
- **Receipt corrections and concurrency.** Verified 861 corrections that replace an earlier quantity (PRD AT33), and simultaneous approvals racing for the same receipt (AT36), aren't implemented yet.
- **Single tenant.** Partner isolation comes from scoping PO lookup to buyer + supplier + PO number. There is no authentication or role-based access.
- **Header charges.** Freight, tax and allowances aren't modeled. Their presence holds the totals check as `NOT_EVALUATED`.
- **watsonx.ai Lite plan.** It allows 300k tokens per month, and services are deleted after 30 days of inactivity.
- **SQLite.** The database is local and single-process. It isn't tamper-resistant audit storage.

## Team

Data Engineering and Parsing · AI Root-Cause Analysis · Software Engineering ·
Systems Integration and Deployment · Business Process Design · Product Testing and Presentation
