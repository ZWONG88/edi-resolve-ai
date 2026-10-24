# Team 3-Way Rules vs. PRD Engine

How the team's *Rules for 3 way Reconciliation* (business rules doc) were merged with the
PRD-based engine, which approach won on each topic, and why. The decisions marked as
**user decision** were made by the project owner on 2026-10-23.

## Rule-by-rule

| Rules doc | Implementation | Notes |
|---|---|---|
| 1.1 R_SCHEMA_VAL | Parser/adapter validation → `QUARANTINED` case, disposition `SCHEMA_VALIDATION_ERROR` | The exact source field is reported. |
| 1.2 R_UOM_NORM | Normalize to the base unit via `data/reference/uom_conversions.json` (`UOM_NORMALIZED`) | Same math as the doc's convert-to-cases, but with no fractional cases. A missing pack size gives `INSUFFICIENT_EVIDENCE` instead of a guess. The Dataset2 pack sizes are **derived from the data (user decision)**: the datasets carry no pack-size field, so each factor comes from the PO/invoice quantity ratio and is cross-checked against the price ratio (e.g. 10 CS ↔ 60 EA and $7.50 ↔ $1.25 both give 6). They're marked "verify". |
| 2.1 R_HEADER_MATCH | PO lookup scoped to buyer + supplier + PO number; no PO → `PO_HEADER_MISMATCH` | Partner scoping is stricter than the doc (PRD AT23). |
| 2.2 R_LINE_MAP | Composite key (line_number, buyer_sku) | The datasets have no UPC, so `buyer_sku` stands in. |
| 3.1 R_UNMAPPED_ITEM | `UNMAPPED_LINE_ITEM`: approved $0.00, 100% blocked, `ADJ_UNMAPPED` | |
| 3.2 R_PRICE_VARIANCE | Invoice above PO price → `PRICE_VARIANCE`; approved at PO price, `ADJ_PRICE` | An invoice below the PO price is shown as informational and paid as invoiced. |
| 3.3 R_SHORTAGE_OVERBILL | Billed > **accepted** receipt qty → `RECEIPT_VARIANCE`, `ADJ_SHORTAGE` | **User decision:** accepted, not received. Damaged or rejected goods also raise `RECEIPT_REVIEW`. |
| 3.4 R_DUPLICATE_INV | An identical resend is a replay no-op; the same invoice number with different content → `DUPLICATE_INVOICE_REJECTED` | Safer than rejecting any repeat number, so an identical resend is harmless. |
| 4.1 R_TOLERANCE_CHECK | Line blocked ≤ $1.00 → waived, `DE_MINIMIS_PASSED`. An invoice header total ≤ $1.00 above its lines is waived too. | Waiving header differences was a **team decision** (2026-10-23). The actual variance is still recorded (PRD 18.1). Configurable via `DE_MINIMIS_LINE_TOLERANCE`. |
| 4.2 R_HIGH_VALUE_HOLD | Blocked > $500 → `NEEDS_HUMAN_AP_REVIEW` | Configurable via `HIGH_VALUE_HOLD_THRESHOLD`. |
| 5.1 R_GLOBAL_DISPOSITION | `AUTO_APPROVED` (blocked = 0) / `APPROVED_WITH_SHORT_PAY` | **User decision:** clean three-way invoices are auto-approved (actor `rules-engine`, audited). |
| 5.2 R_GENERATE_820 | EDI 820 JSON with `ADJ_*` reason codes; paid = invoiced − blocked | **User decision:** generated automatically for auto/short-pay, and after approval for high-value cases. The file is **never transmitted**. |

## Dollar math

Per line, in the normalized base UOM:

```
payable_qty   = min(billed, PO remaining, accepted receipt available)
payable_price = min(invoice price, PO price)
approved      = round_half_up(payable_qty × payable_price), never above the line total
blocked       = invoiced line total − approved
```

This equals the doc's *Price Risk + Shortage Risk* without double counting. When both
apply: (inv − PO) × billed + (billed − accepted) × PO = billed × inv − accepted × PO.
Whatever remains after the price and shortage parts is reported as `ADJ_EXTENSION`
(invoice line-total arithmetic errors).

At the invoice level, the approved total is capped at the invoice header total, and
blocked = header total − approved. A header that disagrees with its own lines is flagged
by the totals check.

## Cases the doc does not cover (the PRD engine decides)

| Situation | Outcome |
|---|---|
| No 861 for the PO | `WAIT_RECEIPT` → `HOLD`. The receipt is unknown, not zero, and there is no silent two-way pass. A later 861 reruns the case automatically. |
| Damaged or rejected goods, receiving review requested | `RECEIPT_REVIEW` → `NEEDS_HUMAN_AP_REVIEW`. Dollars are computed on the accepted quantity. |
| Accepted more than ordered | `OVER_RECEIPT` → `NEEDS_HUMAN_AP_REVIEW`. Extra receipts don't authorize extra billing. |
| Currency mismatch | `INSUFFICIENT_EVIDENCE` → `HOLD`. There is no FX conversion. |
| Missing pack size or price basis | `INSUFFICIENT_EVIDENCE` → `HOLD` |
| Unsupported tax or shipping charges | Totals `NOT_EVALUATED` → `HOLD` |
| Several 861 events without declared incremental/cumulative semantics | `INSUFFICIENT_EVIDENCE` → `HOLD` |

## Open questions for the team

- **Verify the derived pack sizes** for SKU0001–SKU0005 in `data/reference/uom_conversions.json`.
  They're inferred from quantity and price ratios. The dataset author can confirm them.
- **The 820 format** is team-style JSON. If a real X12 820 is needed, it can be rendered from
  the same payload.
- **The master catalogue** (`data/reference/master_catalog.json`) is synthetic and is used only to
  show product names and context. Its prices and pack sizes never affect matching, dispositions or
  820s (PRD §17.3).
