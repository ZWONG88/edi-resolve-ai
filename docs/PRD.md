**Product Requirements Document (PRD)**

**EDI Resolve**

Explainable AI for Retail–Supplier Transaction Reconciliation

Version: 1.0

Status: MVP Definition

Event: SpartHill Hacks 2026 — IBM Responsible AI Track

Product Owner: Product Manager

Development Team: 4 developers, including Data Science ownership

**1\. Product Overview**

EDI Resolve is an AI-assisted reconciliation platform that helps retailers and Consumer Packaged Goods (CPG) suppliers detect, understand, and resolve discrepancies between Electronic Data Interchange (EDI) purchase orders and invoices.

The platform combines deterministic validation rules with evidence-grounded AI explanations and human-controlled resolution workflows.

Product vision: Make EDI exception resolution faster, more understandable, and accessible to supplier teams without requiring extensive EDI expertise.

**2\. Problem Statement**

Retailers and CPG suppliers exchange purchase orders, invoices, and shipment information electronically through EDI. Discrepancies in pricing, units of measure, product identifiers, and quantities can trigger reconciliation exceptions, requiring manual investigation and potentially delaying supplier payments.

Resolving these discrepancies may involve comparing multiple documents, verifying product information, and reviewing contract terms. This creates operational overhead, particularly for small and mid-sized suppliers with limited dedicated EDI resources.

How might we help retailers and CPG suppliers identify, explain, and resolve EDI discrepancies faster using evidence-backed AI recommendations while ensuring transparency, financial accuracy, and human oversight?

**3\. Target Users**

Primary user: EDI / Accounts Receivable Analyst at a CPG supplier

Investigates discrepancies between purchase orders and invoices.

Checks prices, quantities, and product information.

Coordinates corrections or disputes with retail partners.

Needs clear explanations and supporting evidence.

Secondary user: Supplier Operations Manager

Monitors unresolved exceptions.

Reviews escalated discrepancies.

Needs visibility into decisions and resolution status.

**4\. Product Goals**

1\.  Automatically identify supported discrepancies between EDI 850 purchase orders and EDI 810 invoices.

2\.  Explain possible root causes using transaction and reference data.

3\.  Recommend evidence-backed next steps for reconciliation.

4\.  Enable human approval, rejection, or escalation of recommendations.

5\.  Maintain an auditable history of reconciliation decisions.

Non-Goals for the Hackathon

Automatic payment release or financial settlement.

Direct production integration with retailer or supplier ERP systems.

Support for every EDI transaction type or variation.

Full three-way matching with warehouse receiving records.

Production-grade authentication and access management.

**5\. MVP Scope and Priorities**

| Priority | Feature | Description |
| :---- | :---- | :---- |
| P0 | Document ingestion | Upload or select supported EDI 850,861 and 810 samples |
| P0 | EDI parsing | Extract PO number, product identifier, quantity, UOM, price, and totals |
| P0 | Price discrepancy detection | Identify differences between PO and invoice pricing |
| P0 | UOM normalization | Compare cases and eaches using verified product conversion data |
| P0 | AI explanation | Explain mismatches using available supporting evidence |
| P0 | Resolution recommendation | Suggest verification, correction, or escalation |
| P0 | Human review | Approve, reject, or escalate a recommendation |
| P0 | Audit logging | Save reviewer action, reason, evidence, and timestamp |
| P1 | Exception dashboard | Show exception categories, counts, and review status |
| P1 | Product identifier mapping | Resolve supported SKU/UPC cross-references |
| P2 | Three-way matching | Incorporate shipment and receiving records |
| P2 | ERP integration | Connect to real business systems |

**6\. User Journey**

1\.  The analyst opens EDI Resolve.

2\.  The analyst uploads or selects a purchase order and its corresponding invoice.

3\.  The system parses the documents and matches corresponding transaction lines.

4\.  The rules engine compares prices and quantities after applying verified UOM conversions.

5\.  The system displays detected exceptions and the supporting values.

6\.  AI generates an explanation of the discrepancy and possible next steps.

7\.  The analyst reviews the evidence and approves, rejects, or escalates the recommendation.

8\.  The system records the decision and updates the exception status.

7. User Stories

US-01 — Upload Transaction Documents  
As an EDI / Accounts Receivable Analyst, I want to upload or select supported EDI 850 purchase orders and EDI 810 invoices so that I can investigate transaction discrepancies.

Acceptance criteria:

* Valid supported documents load successfully.  
* Invalid or unsupported documents show an understandable error.  
* Original documents remain available as supporting evidence.

US-02 — Match Documents and Line Items  
As an analyst, I want the system to associate invoices with the correct purchase orders and line items so that comparisons use the correct transaction evidence.

Acceptance criteria:

* Documents are linked using trading-partner identity, PO references, and corresponding line-item identifiers.  
* Missing or ambiguous matches are flagged for review instead of silently paired.

US-03 — Detect Price and Quantity Discrepancies  
As an analyst, I want the system to compare prices and quantities using deterministic rules so that I can identify discrepancies accurately.

Acceptance criteria:

* The system displays original values, normalized values, calculated differences, and applicable tolerances.  
* Partial invoices are distinguished from quantities exceeding the remaining PO authorization.  
* Financial calculations are performed in code rather than by AI.

US-04 — Compare Different Units of Measure  
As an analyst, I want quantities and prices expressed in different UOMs to be normalized using verified conversion data so that equivalent transactions are not incorrectly flagged.

Acceptance criteria:

* With a verified conversion of 12 each per case, 50 cases and 600 each are recognized as equivalent quantities.  
* Prices are converted to the same comparable unit.  
* Missing or conflicting conversion data triggers insufficient-evidence review.

US-05 — Understand Possible Root Causes  
As an analyst, I want an evidence-grounded AI explanation of a discrepancy so that I can understand possible causes and decide what to investigate.

Acceptance criteria:

* Explanations reference relevant transaction fields and verified reference data.  
* Possible causes are clearly identified as hypotheses.  
* Unsupported facts are excluded.  
* If AI is unavailable, deterministic findings remain accessible.

US-06 — Handle Insufficient Evidence  
As an analyst, I want the system to identify missing or conflicting evidence so that I know what information is needed before making a decision.

Acceptance criteria:

* The system identifies the missing document, conversion, reference, or policy.  
* Affected checks are marked as not evaluated.  
* The system recommends verification or escalation and does not infer a successful match.

US-07 — Review Recommendations  
As an analyst, I want to approve, reject, or escalate a recommendation so that resolution decisions remain under human control.

Acceptance criteria:

* Every action requires a reviewer-provided reason.  
* Rejection keeps the discrepancy open.  
* Escalation records an accountable owner and next action.  
* Approval records an internal workflow decision without releasing payment or updating external financial systems.  
* Approving an investigation does not automatically resolve the discrepancy.

US-08 — Correct Evidence and Rerun Checks  
As an analyst, I want to provide corrected documents or verified reference data and rerun reconciliation so that decisions reflect the latest evidence.

Acceptance criteria:

* Original evidence and previous results remain in the history.  
* Updated evidence triggers a new reconciliation run.  
* Recommendations based on superseded evidence cannot be approved.

US-09 — Retrieve Decision History  
As an analyst or Supplier Operations Manager, I want to retrieve an exception’s history so that I can understand how and why decisions were made.

Acceptance criteria:

* History includes evidence, findings, recommendations, reviewer actions, reasons, and timestamps.  
* A reviewer action is acknowledged as saved only after its audit record is durably stored.

US-10 — Monitor Exceptions  
As a Supplier Operations Manager, I want to view exception categories and review statuses so that I can prioritize unresolved work.

Priority: P1.

Acceptance criteria:

* The dashboard shows exception counts, categories, and statuses.  
* Users can open an exception to inspect its evidence and history.  
* Matched, unresolved, insufficient-evidence, and technical-hold cases are distinguishable.

US-11 — Reconcile Receiving Evidence  
As an analyst, I want to compare purchase orders and invoices with EDI 861 receiving advice so that I can identify discrepancies involving accepted quantities.

Priority: Conditional P2 stretch goal; not required for P0 completion.

Acceptance criteria:

* Three-way matching is available only when the 861 capability is explicitly enabled.  
* The system distinguishes received, accepted, rejected, and damaged quantities using the supported partner profile.  
* Partial receipts, duplicate receipts, and existing receipt allocations are handled without double-counting.  
* Missing receiving evidence produces a waiting status without silently falling back to a two-way pass.  
* When the capability is disabled, receipt verification is shown as not evaluated.

**7\. Functional Requirements**

**FR-01 — Document Upload**

The system shall accept the team's supported sample EDI 850 and EDI 810 formats, or equivalent normalized sample data.

Acceptance criteria: Valid supported files load successfully. Invalid or unsupported files produce an understandable error.

**FR-02 — Document Matching**

The system shall associate purchase orders and invoices using matching PO references and corresponding line-item identifiers.

Acceptance criteria: Unmatched documents or ambiguous product references are flagged rather than silently paired.

**FR-03 — Price Validation**

The system shall detect price differences after normalizing comparable units and applying defined monetary precision rules.

Acceptance criteria: Price variances are calculated correctly. Available effective-dated contract evidence is displayed separately from the PO price.

**FR-04 — Unit-of-Measure Validation**

The system shall convert supported UOMs using verified product-master data.

Acceptance criteria: If 1 case contains 12 units, 50 cases and 600 units are recognized as equivalent quantities. Missing conversion data triggers review.

**FR-05 — AI Explanation**

The system shall generate a human-readable explanation grounded in the detected exception and supporting documents.

Acceptance criteria: The explanation identifies relevant transaction values and evidence. Missing information is acknowledged. Unsupported facts are not presented as verified.

**FR-06 — Resolution Recommendation**

The system shall recommend a next step, such as verifying a contract, correcting a document, closing a verified equivalent-quantity exception, or escalating.

Acceptance criteria: Every recommendation includes a reason. Financially consequential changes require human review.

**FR-07 — Human Review**

The system shall allow a reviewer to approve, reject, or escalate a recommendation.

Acceptance criteria: The selected action and reviewer-provided reason are saved. Approval records a workflow decision; it does not automatically modify external financial systems.

**FR-08 — Audit Trail**

The system shall store the exception ID, supporting evidence, recommendation, reviewer action, and timestamp.

Acceptance criteria: A reviewer can retrieve the history of an exception.

**8\. Data Requirements**

The MVP requires:

EDI 850 purchase orders.

EDI 810 invoices.

Product master data containing product identifiers and UOM conversion factors.

Price contracts or price reference data, including effective dates where relevant.

Labeled expected outcomes for test scenarios.

Initial dataset: At least 10 synthetic transaction pairs covering valid matches, price mismatches, UOM differences, and insufficient-evidence scenarios.

Data ownership: Data Science teammate.

Product responsibility: PM defines scenarios, business expectations, and acceptance criteria; Data Science and engineering validate the generated data.

**9\. Responsible AI Requirements**

Explainability: Display the source fields and evidence supporting recommendations.

Accuracy: Perform financial calculations in deterministic code, not through an LLM.

Human oversight: Require human review for consequential financial decisions.

Privacy: Use synthetic transactions for the prototype and minimize data sent to AI services.

Fairness: Apply consistent reconciliation policies regardless of supplier identity.

Uncertainty: Escalate cases when reference data is missing, conflicting, or insufficient.

Auditability: Record recommendations and human decisions.

**10\. Sustainability & Social Impact**

EDI Resolve supports economic sustainability by helping retailers and CPG suppliers reduce manual reconciliation effort, investigate invoice discrepancies faster, and potentially minimize payment delays.

The solution particularly focuses on small and mid-sized suppliers that may lack dedicated EDI specialists or access to advanced reconciliation tools. By providing accessible, explainable AI assistance, EDI Resolve aims to promote fairer participation in retail supply chains.

Alignment with UN Sustainable Development Goals:

SDG 8 — Decent Work and Economic Growth: Supports operational efficiency and small-business resilience.

SDG 9 — Industry, Innovation and Infrastructure: Promotes responsible AI adoption in supply-chain operations.

SDG 10 — Reduced Inequalities: Aims to reduce disparities in access to specialized reconciliation capabilities.

Expected Impact: Faster exception investigation, improved transparency, reduced administrative effort, and more consistent reconciliation decisions.

These benefits are projected outcomes and require validation through prototype testing and real-world deployment.

**11\. Success Metrics**

| Metric | Measurement |
| :---- | :---- |
| Exception detection accuracy | Correctly classified test cases / total labeled test cases |
| Resolution recommendation correctness | Correct recommendations / evaluated recommendations |
| Reconciliation time | Median time per case, manual versus assisted, using comparable test scenarios |
| Evidence completeness | Recommendations with verifiable supporting evidence / total recommendations |
| Audit completeness | Reviewed exceptions with complete decision records / total reviewed exceptions |

Note: Hackathon results will be measured on synthetic test data. Production improvements in payment timing or business costs are hypotheses requiring real-world validation.

**12\. Key Risks and Mitigations**

| Risk | Mitigation |
| :---- | :---- |
| AI generates incorrect explanations | Restrict AI to verified structured evidence and test outputs |
| Incorrect price calculations | Use deterministic decimal arithmetic and explicit rounding rules |
| Missing product conversion data | Flag insufficient evidence and escalate |
| Limited EDI format compatibility | Document supported sample structures and limitations |
| Integration delays | Establish common data schemas and API contracts early |
| Insufficient hackathon time | Freeze P2 features and complete the P0 workflow first |

**13\. MVP Definition of Done**

The MVP is complete when:

A supported PO and invoice can be loaded.

Price and UOM mismatches are detected correctly on labeled examples.

The system generates an evidence-backed explanation.

The user can review and record a decision.

An audit history is accessible.

The team has tested at least one uncertain or insufficient-evidence case.

Actual prototype metrics and limitations are documented.

The team can demonstrate the full workflow reliably.

**14\. Future Roadmap**

Phase 1 — Hackathon MVP: Price and UOM reconciliation with explainable AI and human review.

Phase 2 — Expanded reconciliation: Product identifier matching, discounts, allowances, and more EDI variations.

Phase 3 — Enterprise readiness: Three-way matching, ERP integration, role-based access, security controls, and supplier collaboration workflows.

Core value proposition: EDI Resolve moves beyond simply flagging transaction mismatches by helping users understand the evidence, investigate root causes, and make accountable reconciliation decisions.

**15 Scope clarification and release priorities**

This clarification resolves a source inconsistency without silently expanding the hackathon MVP. Section 5 lists 850,861 and 810 ingestion as P0; sections 4, 5, 7, 8, 13, and 14 otherwise define an 850/810 baseline and defer full three-way matching. The following interpretation governs the added design.

| Capability | Release treatment | Completion evidence |
| :---- | :---- | :---- |
| 850 and 810 ingestion and parsing | P0 baseline | Supported fixtures load, validate, link, and preserve source fields. |
| Two-way reconciliation | P0 baseline | Price, UOM, and quantity checks; insufficient evidence; human actions; complete audit. |
| 861 file availability | Source P0 ingestion wording retained | Optional sample upload or storage alone does not mean receipt matching is implemented. |
| 861 semantic parsing and three-way matching | Conditional P2 stretch goal | Approved partner profile, receipt semantics, allocation ledger, and all P2 tests pass. |
| ERP updates and payment release | ERP integration remains P2; automatic settlement excluded | No approval action posts, pays, transmits a correction, or mutates an external ledger. |

**Conditional enablement**

The Product Manager and engineering lead record whether the 861 stretch goal is enabled, its supported sample profile, and the test results. Default three\_way\_enabled is false. The source still describes full production warehouse matching as a non-goal; the stretch goal is a bounded synthetic demonstration, not a production integration commitment.

When disabled, store or reject an 861 with an explicit unsupported-for-reconciliation message. Display receipt verification as NOT\_EVALUATED. Never claim a three-way pass. When enabled, a case requiring three-way checks waits for valid receiving evidence; it must not silently fall back to two-way matching.

**Transaction meaning**

850 supplies ordering evidence; 810 supplies billing evidence; 861 supplies receiving or acceptance evidence. An 861 is not a shipment notice or proof of payment. Received, accepted, rejected, and damaged quantities must retain their distinct partner-defined meanings. The X12 supply chain reference explains the receipt role of 861; exact supported fields depend on the chosen version and partner guide \[1, 2\].

**Priority boundaries**

P0 links exact supported product IDs and flags ambiguity. Broader SKU/UPC mapping remains P1. Quantity checks make the existing user journey explicit; they do not add discounts, allowances, taxes, arbitrary split allocations, or production receipt integration to the baseline. Any unsupported pricing component is surfaced for review.

**16 System design flowchart**

The rules engine owns all matching outcomes and calculations. AI explains exceptions after checks complete. Every state transition writes an audit event; approval remains an internal workflow decision.

![][image1]  
**Figure 1\. System flow for the P0 baseline and conditional P2 receipt path. WAIT and REVIEW branches re-enter linking and checks only when new verified evidence is supplied.**

**17 Ingestion and evidence contracts**

**17 1 Validation and provenance**

Accept only explicitly supported X12 versions and partner profiles, or labeled normalized synthetic fixtures. Check file type and limits, interchange/group/transaction boundaries, control identifiers and counts, required fields, numeric syntax, and document purpose. Unsupported cancellations, replacements, returns, credits, or negative quantities are held unless their semantics are implemented in the profile. A technical acknowledgment, if added later, is not business acceptance.

Store immutable original bytes, content hash, ingestion time, tenant and trading partner, parser version, validation results, and raw segment/element or fixture-field pointers. Retain original units, currency, precision, identifiers and dates alongside normalized values. No guessed default currency, UOM, price basis, receipt status, or product mapping is permitted.

| Record | Required canonical fields |
| :---- | :---- |
| Common envelope | tenant, sender, receiver, partner\_profile, X12 version, transaction type, control IDs, document ID/version, content hash, source pointers |
| 850 order | PO ID, order date, buyer/supplier, ship-to, currency, PO line ID, qualified product ID, ordered quantity/UOM, unit price and price basis |
| 810 invoice | Invoice ID/date, PO reference(s), line reference, qualified product ID, quantity/UOM, unit price and basis, currency, line extensions, supported charges and total |
| 861 receipt in P2 | Receipt ID/date, PO/line and item references, facility, receipt event/version, quantities and UOM by received/accepted/rejected/damaged status, correction semantics |
| Reference snapshot | Verified item cross-references, pack conversions, effective dates, source/approver, partner scope, price references, policy version and validity interval |
| Reconciliation case | Mode, evidence versions, candidate and confirmed links, findings, calculated deltas, allocations, AI result, reviewer state and audit history |

**17 2 Linking and deduplication**

Link within one tenant and trading relationship using PO reference, supplier/buyer identity, currency, facility where required, and PO line reference plus verified product ID. Exact unique matches are allowed; unmatched, conflicting, or multiple candidate lines require review. Repeated SKUs are not interchangeable. No fuzzy AI linkage or cross-partner match may become a verified link.

A repeated content hash is an idempotent replay. The same business document key with different content is a duplicate or revision conflict, not a second payable invoice. A profile must distinguish a verified replacement from an additional transaction. Split invoices, receipts, and multi-PO documents need explicit line allocations; ambiguous allocations remain held. Re-ingestion never double-counts quantity.

**17 3 Reference authority**

Use approved partner product-master conversions and contractual data at the applicable date. The profile declares that date; the prototype uses the order date unless a fixture explicitly states otherwise. Missing, expired, overlapping, or contradictory records yield INSUFFICIENT\_EVIDENCE. External catalogs such as Open Food Facts can enrich descriptions but cannot establish pack size, agreed price, receipt truth, or identity for financial decisions. Sales data does not prove receipt.

**18 Deterministic reconciliation rules**

**18 1 Comparison policy**

Pin the profile, reference snapshot, applicable date, precision, rounding, tolerances and mode for every run. The proposed synthetic baseline uses Decimal arithmetic, USD with two decimal places, ROUND\_HALF\_UP at line extension, no unit-price rounding before extension, and zero quantity and price tolerance. These are prototype choices, not universal trading rules. Missing required policy configuration blocks a verified result. Currency mismatch is held; no implicit FX conversion occurs.

If the approved policy later permits tolerances, persist both the actual variance and the tolerance applied. Define price acceptance as abs(delta\_unit\_price) \<= max(price\_abs\_tolerance, abs(PO\_price\_base) × price\_relative\_tolerance), and quantity acceptance as abs(delta\_quantity) \<= quantity\_tolerance. Tax, freight, discounts, allowances and header rounding require explicit supported treatment; otherwise mark totals NOT\_EVALUATED and hold the affected result.

**18 2 Normalization and two way checks**

For each uniquely linked line, select a common base UOM. Let f be the verified number of base units per transaction UOM, and b be the number of those UOMs covered by the quoted price. Compute Q\_base \= Q\_raw × f and P\_base \= P\_raw / (b × f). Require f \> 0 and b \> 0; exact same units use f \= 1 only when their meaning is established. An unknown conversion or price basis blocks comparable price and quantity conclusions, while independent checks may still report findings.

Compute delta\_price \= P\_invoice\_base \- P\_PO\_base and delta\_quantity \= Q\_invoice\_base \- Q\_PO\_allocated\_base. The PO allocation represents the portion covered by this invoice. Separately check cumulative distinct invoice allocations against the PO authorization. A partial invoice below the order is a partial-billing status, not automatically a shortage. An invoice above the available PO balance is a quantity exception. Do not close an entire PO based on one invoice.

Compute expected invoice extension \= round\_money(Q\_invoice\_base × P\_PO\_base) and actual extension from supported invoice price basis. Validate invoice-provided extensions and supported totals independently. Display PO price, invoice price, contract price, variance and evidence separately. A contract difference may explain stale pricing but never overwrites the PO or establishes an authorized amendment.

**18 3 Three way checks when P2 is enabled**

Only receipt evidence with verified partner semantics is eligible. Received quantity is not necessarily accepted quantity. Retain distinct status counts and avoid adding overlapping received and accepted values. The profile must define which accepted quantity is billable, how damage/rejection affects it, and whether an 861 reports incremental events or cumulative snapshots. If it cannot, hold for insufficient evidence.

Build a deduplicated receipt ledger by PO line, item, facility and event/version. Sum eligible incremental events, or select the current valid cumulative snapshot; never sum successive snapshots. Apply explicit reversals/replacements, retain superseded versions, and subtract existing approved or reserved allocations without subtracting overlapping status counts twice. An ambiguous correction is held. Allocate eligible receipts atomically to prevent two invoices claiming the same receipt.

For an invoice line, require its base quantity to be covered by unallocated eligible receipts and within the remaining PO authorization. Less than the full PO may pass as a partial invoice if both cover it. Missing receipts yield WAIT\_RECEIPT, never zero received. Invoice quantity above accepted coverage yields RECEIPT\_VARIANCE; accepted coverage above the order yields OVER\_RECEIPT review. Rejected/damaged stock or unresolved receipt conditions remain visible and block a verified three-way pass. A later receipt reruns the case; it does not erase the earlier finding.

**19 Discrepancy decision table**

Evaluate every applicable deterministic rule. Multiple findings may coexist. Blocking validation or ambiguous evidence prevents a verified pass; AI does not resolve a blocking condition. CHECK\_NOT\_EVALUATED must remain distinct from CHECK\_FAILED.

| Condition | Deterministic result | Evidence and permitted next step |
| :---- | :---- | :---- |
| Malformed envelope, unsupported profile or missing required numeric field | QUARANTINED | Show exact source error; request supported or corrected file. No AI reconstruction of transaction facts. |
| Exact duplicate / conflicting same business key | REPLAY\_NOOP / DUPLICATE\_CONFLICT | Return original case for replay; hold differing payloads pending verified revision handling. |
| Missing PO or ambiguous line/product link | WAIT\_DOCUMENT or REVIEW\_LINK | Show candidates and missing keys. Analyst selects only with evidence; rerun deterministic checks. |
| Unknown UOM factor, price basis, currency, or contradictory references | INSUFFICIENT\_EVIDENCE | Show original fields and missing or conflicting references; request verified mapping or policy. Do not calculate a guessed variance. |
| 50 CS vs 600 EA, verified 12 EA per CS; prices also equivalent | No UOM or quantity mismatch | Show normalized equality. If all other checks pass, MATCHED\_2WAY; receipt status remains NOT\_EVALUATED in P0. |
| Comparable normalized invoice price differs from PO beyond tolerance | PRICE\_VARIANCE | Show PO and invoice values/delta. AI may suggest stale price only with dated contract evidence; human verifies correction or escalates. |
| Invoice below PO remaining balance, valid explicit allocation | PARTIAL\_BILLING | Record allocated amount and open PO balance. No shortage claim solely from this difference. |
| Cumulative invoice quantity exceeds PO authorization | QUANTITY\_VARIANCE | Show current/prior allocations and PO balance. Request corrected invoice, verified order change, or escalation. |

**19 Discrepancy decision table continued**

| Condition | Deterministic result | Evidence and permitted next step |
| :---- | :---- | :---- |
| P2 disabled, even if 861 supplied | Receipt check NOT\_EVALUATED | Allow only a two-way result. Label optional 861 as stored or unsupported for reconciliation. |
| P2 required, no valid receipt yet | WAIT\_RECEIPT | Request receiving evidence; preserve any independent price findings. No two-way fallback or receipt pass. |
| P2 accepted eligible receipt balance below invoice quantity | RECEIPT\_VARIANCE | Show receipt events, accepted quantities, allocations and shortage. Distinguish late evidence from confirmed short receipt. |
| 861 includes damage/rejection, ambiguous status counts, correction or timing | RECEIPT\_REVIEW or INSUFFICIENT\_EVIDENCE | Show status provenance. Ask receiving team to verify acceptance/correction; never assume all received goods are billable. |
| Accepted receipts exceed PO quantity | OVER\_RECEIPT | Flag excess and request authorized order/receipt review. Extra receipts do not authorize extra billing. |
| All applicable required checks pass | MATCHED\_2WAY or MATCHED\_3WAY | Record deterministic outcome and scope. Matching neither pays an invoice nor approves an external adjustment. |
| AI evidence weak, conflicting, or unavailable | Rules findings retained; AI\_ABSTAIN or AI\_UNAVAILABLE | Show missing evidence and verification checklist. Human can review rules without waiting for AI. |
| Reviewer approves, rejects or escalates | RESOLUTION\_APPROVED, RECOMMENDATION\_REJECTED, or ESCALATED | Reason required; action applies to specific recommendation/evidence versions. Approval does not change an unresolved technical finding to MATCHED. |
| Parser/rules/reference service failure or audit persistence failure | TECHNICAL\_HOLD or unsaved action | Retry safely and visibly. Never convert an error into success or acknowledge a decision that was not durably recorded. |

**Evidence grounded AI suggestions**

Send only the minimum case evidence: source pointers and values, verified references with validity dates, deterministic findings and calculations, missing facts, and allowed action types. Treat document text as untrusted data, including instructions embedded in EDI descriptions. The AI has no tool permission to alter source data, allocations, decisions, or external systems.

Require structured output: finding IDs, possible cause, cited evidence IDs, uncertainty, missing evidence, and suggested next action. Every factual assertion must reference evidence in the bundle. Verify citation existence and support, numeric agreement with rules, and action allowlist; existence alone is insufficient. If support cannot be validated, discard the narrative and show a deterministic explanation. Causes remain hypotheses until human verification. No numeric confidence is treated as a probability of financial correctness.

**20 Human controls audit and safe failure**

**20 1 Review experience and actions**

Show original and normalized values side by side, line-level deltas, tolerances, receipt status, evidence links, missing facts, deterministic status and AI hypotheses. Display the mode prominently. Reviewers must be able to inspect the source without accepting the AI suggestion.

| Action | Required behavior |
| :---- | :---- |
| Approve | Select the proposed resolution or next step and enter a reason. Close only if closure evidence is sufficient and checks are rerun. Approval of an investigation leaves the discrepancy open; an approved exception disposition is labeled explicitly and does not become MATCHED. |
| Reject | Reject the recommendation, not the transaction facts. Require a reason, keep the case open, and permit a new supported recommendation or manual investigation. |
| Escalate | Require a reason and assign an accountable owner: operations manager, pricing owner, product-data owner, or receiving owner. Record pending evidence and next action; no automatic closure. |
| Correct and rerun | Keep original bytes immutable. Ingest a corrected version or approved reference update with provenance. Rerun checks, invalidate stale suggestions, and require review of the current evidence version. |

**20 2 Audit requirements**

Append events for ingestion, validation, deduplication, linking, reference selection, rule execution, AI request/result/abstention, human action, retries, version supersession and state transitions. Each event contains event and case IDs, correlation/idempotency key, actor or service, UTC timestamp, before/after state, document hashes and versions, source pointers, reference and policy versions, calculations and findings, recommendation/model/prompt versions, reviewer reason and escalation owner where relevant.

Persist an action, its allocation changes, case version and audit event atomically. If audit persistence fails, roll back and display Action not saved. Retain previous runs and decisions as append-only history, including superseded recommendations. The prototype must demonstrate durable retrieval; production retention, tamper-resistant storage and full identity/access controls require a later policy and implementation.

**20 3 Safe failure behavior**

Parsing or rule failures enter a visible technical hold and never pass. Retry transient failures with bounded backoff and idempotency; after the configured retry limit route to technical review. Missing receipt evidence stays pending with an assigned owner. The prototype default is three attempts for transient service errors and a 24-hour receipt follow-up flag; these values must be configurable and do not authorize a fallback match.

AI failure leaves deterministic results and manual review available. Disable the affected comparison when reference data is unavailable; never substitute web data or invented pack sizes. Review uses optimistic version checks and ledger locks; concurrent or stale actions must retry against current evidence. Cancellation or application restart resumes from durable checkpoints. No step releases funds, changes external documents, or silently treats rejected quantities as accepted.

**21 Acceptance tests for the P0 baseline**

These are specified acceptance tests, not claims that a prototype has executed them. Use labeled synthetic fixtures and assert the case state, line findings, normalized values, evidence pointers and audit events. Unless stated otherwise, use USD, zero tolerances, Decimal arithmetic and the policy in section 18\. Each fixture is isolated unless it names prior allocations.

| ID | Fixture and action | Expected result |
| :---- | :---- | :---- |
| AT01 | 850: 100 EA at \$2; 810: 100 EA at \$2, total \$200. | MATCHED\_2WAY; no exception; receipt NOT\_EVALUATED; no payment action. |
| AT02 | 850: 50 CS at \$24/CS; 810: 600 EA at \$2/EA; verified 12 EA/CS. | Both normalize to 600 EA and \$2/EA; total \$1,200; no UOM or quantity variance. |
| AT03 | Same documents as AT02; conversion absent, expired or conflicting. | INSUFFICIENT\_EVIDENCE; dependent checks NOT\_EVALUATED; no guessed factor or price diagnosis. |
| AT04 | 850: 100 EA at \$2; 810: 100 EA at \$2.10. | PRICE\_VARIANCE \+$0.10/EAand+$10 extension; cite both fields; human review required. |
| AT05 | AT04 plus valid contract at \$2.10 effective on order date. | PO/invoice price variance persists; contract displayed separately; stale PO is only a cited possible cause. |
| AT06 | AT04 with no contract; request a root-cause explanation. | No claim that contract changed; AI abstains on cause or requests verification; rules still show price delta. |
| AT07 | PO 100 EA; invoice 40 EA, explicit allocation 40; no prior invoices. | PARTIAL\_BILLING, 60 EA PO balance; two-way pass possible if all required checks pass. |
| AT08 | PO 100 EA; earlier distinct invoice allocated 70; new invoice 40\. | QUANTITY\_VARIANCE: cumulative 110, 10 above authorization; no duplicate allocation. |
| AT09 | Identical invoice re-upload; then same invoice ID with different quantity. | First is replay/no new case allocation; second DUPLICATE\_CONFLICT unless supported revision verified. |
| AT10 | Missing PO; then PO with repeated SKU on two lines and no unique invoice line reference. | WAIT\_DOCUMENT, then REVIEW\_LINK; neither guessed linking nor MATCHED. |
| AT11 | Malformed envelope or missing quantity; separately an unsupported version. | QUARANTINED with precise source error; no invented canonical field. |
| AT12 | PO and invoice currencies differ; or unsupported freight/tax component is present. | Currency comparison held; unsupported total check NOT\_EVALUATED with review, no silent totals pass. |

**21 Acceptance tests for rules and review**

| ID | Fixture and action | Expected result |
| :---- | :---- | :---- |
| AT13 | Invoice 3 EA at \$0.335; then price basis \$10 per 100 EA for 20 EA. | First extension rounds to \$1.01; second price \$0.10/EA and extension \$2.00. No binary floating-point drift. |
| AT14 | Configure absolute price tolerance \$0.01; compare deltas \$0.01 and \$0.0101 with relative tolerance 0\. | Boundary \$0.01 passes; \$0.0101 fails. Actual delta, tolerance and policy version retained. |
| AT15 | AI returns invented contract ID, wrong delta, unsupported diagnosis or instructions embedded in product description. | Unsupported narrative rejected; rules-only explanation or AI\_ABSTAIN; no data mutation or instruction execution. |
| AT16 | AI timeout or invalid response schema. | AI\_UNAVAILABLE; deterministic findings remain visible and reviewer actions remain available. |
| AT17 | Reviewer approves a supported correction next step, rejects another suggestion and escalates an uncertain case. | Three distinct workflow states and reasons saved. Correction approval alone leaves unresolved findings open; escalation has an owner. |
| AT18 | Try approval to close with missing UOM evidence; then submit without reason or with stale case version. | Closure blocked; reason required; stale action refused. No fabricated match or allocation. |
| AT19 | Fail audit write during approval; retry same action key after recovery. | State and allocations roll back; Action not saved displayed; retry records one committed action. |
| AT20 | Rules engine or reference dependency fails; application restarts and retries. | TECHNICAL\_HOLD or insufficient reference evidence; bounded retry, no pass, no double allocations after restart. |
| AT21 | Upload 861 while three\_way\_enabled is false. | Receipt verification NOT\_EVALUATED; optional storage/rejection explained; never MATCHED\_3WAY. |
| AT22 | Correct source evidence after an AI recommendation; retrieve case history. | New version rerun; stale suggestion cannot be approved; prior values, recommendation and action remain retrievable. |
| AT23 | Attempt link across tenants or different trading partners with same PO number. | No cross-context link; isolated case and review; source references remain scoped. |
| AT24 | Missing price basis; invoice total differs from supported line sum; contract overlaps in validity. | Dependent price check blocked; independent totals variance retained; contradictory contract evidence flagged. |

**21 Acceptance tests for conditional P2 receipts**

Run these only when the 861 feature gate is enabled. For numeric receipt tests, PO price and invoice price are equal and all other required checks pass. Accepted quantities are billable under the explicit test partner profile.

| ID | Fixture and action | Expected result |
| :---- | :---- | :---- |
| AT25 | PO 100 EA, invoice 100 EA, valid 861 accepted 100 EA. | MATCHED\_3WAY; unique receipt linkage and allocation recorded. |
| AT26 | PO 100 EA, invoice 100 EA, no valid receipt. | WAIT\_RECEIPT; receipt unknown, not zero; no silent two-way fallback. |
| AT27 | PO 100 EA, invoice 100 EA, received 100 EA but accepted 90 EA and rejected 10 EA. | RECEIPT\_VARIANCE short 10 plus RECEIPT\_REVIEW; no pass based on physical received count. |
| AT28 | PO 100 EA, invoice 40 EA, accepted receipt 40 EA. | Three-way partial invoice can pass for 40; remaining PO 60\. No claim full PO was received. |
| AT29 | Incremental receipt events 40 and 60 EA; replay second event. | Eligible total 100, not 160; replay does not duplicate receipt or invoice allocations. |
| AT30 | Cumulative snapshots 40 then 100 EA under declared snapshot semantics. | Current eligible total 100, not 140; previous snapshot retained as superseded. |
| AT31 | Accepted 100 EA; approved invoice allocation 60; another invoice asks for 50\. | Receipt balance 40; RECEIPT\_VARIANCE 10\. Concurrent approvals cannot allocate more than 100\. |
| AT32 | PO 100 EA; accepted 110; invoice 110\. | OVER\_RECEIPT and QUANTITY\_VARIANCE 10; excess receipt does not authorize billing. |
| AT33 | Initial receipt accepted 100; verified supported correction replaces with 80\. | Ledger becomes 80, no addition; linked invoices rerun; earlier match marked superseded and shortage exposed. |
| AT34 | 861 has ambiguous status qualifiers, unknown correction semantics, missing line link or unknown UOM. | INSUFFICIENT\_EVIDENCE or REVIEW\_LINK; required receipt check not passed. |
| AT35 | AT26 receives valid accepted quantity later; then repeat processing. | New evidence run can pass if all checks pass; earlier wait remains in history; repeat is idempotent. |
| AT36 | Two simultaneous invoices each request 60 against 100 accepted; both try approval. | Atomic ledger check allows at most 100 total committed/reserved quantity; second action refreshes or stays held. |

**22 Release gates ownership and references**

**22 1 Release gates**

P0 completion requires the source definition of done plus AT01 to AT24 on supported synthetic fixtures. Record actual outcomes, not only planned coverage. All deterministic financial examples, insufficient-evidence safeguards, human actions, and audit atomicity cases must pass. AI must never introduce an unsupported verified fact, and no test may produce an external financial side effect. Keep at least 10 transaction pairs as required by the source; reuse or extend them into the larger scenario suite.

P2 enablement additionally requires AT25 to AT36, a declared supported 861 profile, accepted-versus-received semantics, incremental/cumulative handling, correction rules, receipt allocation concurrency, and an explicit PM/engineering enablement record. If these conditions are unmet, the release remains P0 with receipt checks visibly NOT\_EVALUATED.

**22 2 Ownership and measurement**

The Product Manager owns scope, expected outcomes, tolerance decisions and release interpretation. Data Science owns labeled fixtures and validates reference provenance with engineering. Engineering owns supported parsing profiles, deterministic calculations, allocation and audit consistency. The analyst records review decisions; the operations manager owns escalations. These responsibilities elaborate the existing source roles without adding staffing commitments.

Report the source metrics separately for P0 and optional P2. Detection accuracy must include insufficient-evidence and unsupported cases; report per-category outcomes so a majority of easy matches cannot hide misses. Measure evidence completeness against verifiable cited facts and audit completeness against all required fields. Compare manual and assisted time on the same scenarios. No production payment-timing or savings claim is established by synthetic results.

**22 3 Decisions required before implementation**

Confirm the supported X12 version and partner guide for each transaction type; document exact segment mappings and supported document purposes.

Approve the synthetic currency, monetary precision, price basis, tolerances, reference effective-date policy and treatment of charges.

If P2 is enabled, approve billable receipt statuses, receipt correction/snapshot semantics, allocation ownership and follow-up timing.

Record actual test evidence and demonstrate full P0 review and audit retrieval before attempting P2.

**22 4 Source and technical references**

Source PRD: SpartHill Group\_15 PRD.pdf, version 1.0, seven pages. Original sections 1 to 14 are retained in this revision. Original source pagination differs from the editable reflow; no original section is intentionally removed.

\[1\] X12. Supply Chain Transaction Flow. https\://x12.org/index.php/flow/supply-chain (accessed 23 October 2026). Used for the business role of ordering, billing and receiving transactions.

\[2\] X12. X12 Transaction Sets. https\://x12.org/products/transaction-sets (accessed 23 October 2026). Authoritative transaction names and purposes; not a substitute for the licensed standard and selected partner implementation guide.

All reconciliation policies, architecture, state names, pseudocode and acceptance fixtures in sections 15 to 23 are proposed product requirements. They are not claims that X12 mandates this application design.

EDI Resolve  \|


[image1]: <data:image/png;base64,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>
