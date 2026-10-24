"""Deterministic two-way rules engine (PRD sections 18-19). No AI, no floats, no file I/O.

Normalization: Q_base = Q_raw * f, P_base = P_raw / (b * f), where f is verified base
units per transaction UOM and b is the number of transaction UOMs the price covers.
Money is rounded with ROUND_HALF_UP only at line extension.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from backend.exceptions import ReconciliationError
from backend.models import (
    STATE_PRECEDENCE,
    CaseResult,
    CheckStatus,
    Evidence,
    Finding,
    FindingStatus,
    Invoice,
    InvoiceLine,
    LineResult,
    Money,
    POLine,
    PriceContract,
    PurchaseOrder,
    ReconciliationPolicy,
    UOMConversion,
)
from backend.reference import get_contract_price, get_conversion

CENT = Decimal("0.01")
ZERO = Decimal("0")
PASSED = CheckStatus.CHECK_PASSED
FAILED = CheckStatus.CHECK_FAILED
NOT_EVALUATED = CheckStatus.CHECK_NOT_EVALUATED

# Checks that depend on a verified UOM factor and price basis.
PRICE_CHECKS = ("unit_price", "extension_vs_po", "invoice_extension_arithmetic")


def round_money(value: Decimal) -> Decimal:
    """Round to cents with ROUND_HALF_UP. Use only at line extension / totals."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _s(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f") if value == value.to_integral() else str(value)


def _signed(value: Decimal) -> str:
    text = _s(value) or "0"
    return text if value < 0 else f"+{text}"


def _signed_money(value: Decimal) -> str:
    text = str(value.quantize(CENT))
    return text if value < 0 else f"+{text}"


def case_state(states: list[FindingStatus | None]) -> FindingStatus:
    present = {s for s in states if s is not None}
    for state in STATE_PRECEDENCE:
        if state in present:
            return state
    return FindingStatus.MATCHED_2WAY


class _Run:
    """Mutable state for one comparison: finding IDs and the evidence registry."""

    def __init__(self) -> None:
        self.n = 0
        self.evidence: dict[str, Evidence] = {}

    def next_id(self) -> str:
        self.n += 1
        return f"F{self.n:03d}"

    def add(self, evidence_id: str, source: str, field: str, raw: str,
            normalized: str | None = None) -> None:
        self.evidence[evidence_id] = Evidence(
            evidence_id=evidence_id, source_doc_id=source, field_name=field,
            raw_value=raw, normalized_value=normalized,
        )

    def subset(self, ids: list[str]) -> list[Evidence]:
        return [self.evidence[i] for i in dict.fromkeys(ids) if i in self.evidence]


def _link_line(inv_line: InvoiceLine, po: PurchaseOrder) -> tuple[list[POLine], str]:
    """Return candidate PO lines and how they were found. Never guesses."""
    if inv_line.line_reference:
        exact = [pl for pl in po.lines if pl.line_id == inv_line.line_reference]
        if exact:
            return exact, "line_reference"
    by_product = [pl for pl in po.lines if pl.product_id == inv_line.product_id]
    if by_product:
        return by_product, "product_id"
    if inv_line.upc:
        by_upc = [pl for pl in po.lines if pl.upc and pl.upc == inv_line.upc]
        if by_upc:
            return by_upc, "upc"
    return [], "none"


def _normalize_factors(
    product_id: str,
    po_uom: str,
    inv_uom: str,
    uom_ref: list[UOMConversion],
    on_date: date,
) -> tuple[Decimal, Decimal, str] | None:
    """(f_po, f_inv, base_uom), or None if no verified conversion exists."""
    if po_uom == inv_uom:
        return Decimal("1"), Decimal("1"), po_uom
    factor = get_conversion(product_id, po_uom, inv_uom, uom_ref, on_date)
    if factor is not None:
        return factor, Decimal("1"), inv_uom
    return None


def compare(
    po: PurchaseOrder,
    invoice: Invoice,
    uom_ref: list[UOMConversion],
    price_ref: list[PriceContract],
    policy: ReconciliationPolicy | None = None,
    prior_allocated: dict[str, Decimal] | None = None,
    case_id: str = "pending",
) -> CaseResult:
    """Run every applicable P0 check. Multiple findings may coexist."""
    policy = policy or ReconciliationPolicy.baseline()
    prior_allocated = dict(prior_allocated or {})
    run = _Run()
    run.add("po.number", po.po_id, "po_number", po.po_id)
    run.add("po.date", po.po_id, "po_date", po.order_date.isoformat())
    run.add("po.currency", po.po_id, "currency", po.currency)
    run.add("inv.number", invoice.invoice_id, "invoice_number", invoice.invoice_id)
    run.add("inv.currency", invoice.invoice_id, "currency", invoice.currency)
    run.add("policy.version", "policy", "policy_version", policy.policy_version)
    header: list[Finding] = []
    lines: list[LineResult] = []
    allocations: list[dict[str, str]] = []

    currency_ok = po.currency == invoice.currency
    header.append(
        Finding(
            finding_id=run.next_id(),
            check="currency",
            status=PASSED if currency_ok else NOT_EVALUATED,
            result_state=None if currency_ok else FindingStatus.INSUFFICIENT_EVIDENCE,
            message="Currencies match."
            if currency_ok
            else f"Currency mismatch ({po.currency} vs {invoice.currency}); "
            "held, no FX conversion. Price and extension checks not evaluated.",
            po_value=po.currency,
            invoice_value=invoice.currency,
            evidence_ids=["po.currency", "inv.currency"],
        )
    )

    line_amounts: list[Decimal | None] = []
    for inv_line in invoice.lines:
        line, amount = _compare_line(
            inv_line, po, invoice, uom_ref, price_ref, policy, prior_allocated,
            currency_ok, run, allocations,
        )
        lines.append(line)
        line_amounts.append(amount)

    header.append(_totals_check(invoice, line_amounts, run))
    header.append(
        Finding(
            finding_id=run.next_id(),
            check="receipt",
            status=NOT_EVALUATED,
            result_state=None,
            message="Receipt (861) verification not evaluated: two-way P0 mode.",
        )
    )

    states = [f.result_state for f in header] + [ln.status for ln in lines]
    return CaseResult(
        case_id=case_id,
        run_id=uuid.uuid4().hex,
        case_version=1,
        po_id=po.po_id,
        invoice_id=invoice.invoice_id,
        overall_status=case_state(states),
        line_results=lines,
        policy_version=policy.policy_version,
        run_timestamp=datetime.now(timezone.utc),
        header_findings=header,
        evidence=list(run.evidence.values()),
        allocations=allocations,
    )


def _finish(line: LineResult, run: _Run) -> LineResult:
    line.checks_not_evaluated = [f.check for f in line.findings if f.status == NOT_EVALUATED]
    line.status = case_state([f.result_state for f in line.findings])
    line.evidence = run.subset([e for f in line.findings for e in f.evidence_ids])
    return line


def _compare_line(
    inv_line: InvoiceLine,
    po: PurchaseOrder,
    invoice: Invoice,
    uom_ref: list[UOMConversion],
    price_ref: list[PriceContract],
    policy: ReconciliationPolicy,
    prior_allocated: dict[str, Decimal],
    currency_ok: bool,
    run: _Run,
    allocations: list[dict[str, str]],
) -> tuple[LineResult, Decimal | None]:
    iv = f"inv.{inv_line.line_id}"
    for key, value in inv_line.raw.items():
        if key != "description":  # description is untrusted free text
            run.add(f"{iv}.{key}", invoice.invoice_id, key, value)
    line = LineResult(
        line_id=f"LR-{inv_line.line_id}",
        invoice_line_id=inv_line.line_id,
        status=FindingStatus.MATCHED_2WAY,
        invoice_value_raw=dict(inv_line.raw),
        tolerance_applied=policy.policy_version,
    )
    findings = line.findings
    provided_amount = inv_line.extended_price.amount if inv_line.extended_price else None

    candidates, how = _link_line(inv_line, po)
    if len(candidates) != 1:
        msg = (
            f"No PO line matches product {inv_line.product_id} / UPC {inv_line.upc or '-'}."
            if not candidates
            else f"{len(candidates)} PO lines match by {how} "
            f"({', '.join(c.line_id for c in candidates)}); no unique line reference."
        )
        findings.append(
            Finding(finding_id=run.next_id(), check="line_link", status=NOT_EVALUATED,
                    result_state=FindingStatus.REVIEW_LINK, message=msg,
                    evidence_ids=[f"{iv}.product_id"])
        )
        for check in ("quantity", *PRICE_CHECKS):
            findings.append(_blocked(run, check, "line link is not unique"))
        return _finish(line, run), provided_amount

    po_line = candidates[0]
    pv = f"po.{po_line.line_id}"
    for key, value in po_line.raw.items():
        if key != "description":
            run.add(f"{pv}.{key}", po.po_id, key, value)
    line.po_line_id = po_line.line_id
    line.po_value_raw = dict(po_line.raw)
    findings.append(
        Finding(finding_id=run.next_id(), check="line_link", status=PASSED, result_state=None,
                message=f"Linked to PO line {po_line.line_id} by {how}.",
                evidence_ids=[f"{pv}.line_id", f"{iv}.line_id"])
    )

    _contract_display(po_line, po, price_ref, run, line)

    # UOM normalization (f)
    try:
        factors = _normalize_factors(
            po_line.product_id, po_line.uom, inv_line.uom, uom_ref, po.order_date
        )
        conflict = None
    except ReconciliationError as exc:
        factors, conflict = None, str(exc)
    if factors is None:
        findings.append(
            Finding(
                finding_id=run.next_id(), check="uom_conversion", status=NOT_EVALUATED,
                result_state=FindingStatus.INSUFFICIENT_EVIDENCE,
                message=conflict
                or f"No verified {po_line.uom}->{inv_line.uom} conversion for "
                f"{po_line.product_id}; no factor guessed.",
                po_value=po_line.uom, invoice_value=inv_line.uom,
                evidence_ids=[f"{pv}.uom", f"{iv}.uom"],
            )
        )
        for check in ("quantity", *PRICE_CHECKS):
            findings.append(_blocked(run, check, "UOM conversion missing or conflicting"))
        return _finish(line, run), provided_amount

    f_po, f_inv, base_uom = factors
    if f_po != 1:
        run.add(f"ref.uom.{po_line.product_id}", "reference:uom_conversions", "factor",
                f"1 {po_line.uom} = {_s(f_po)} {base_uom}")
    findings.append(
        Finding(finding_id=run.next_id(), check="uom_conversion", status=PASSED,
                result_state=None,
                message=f"Normalized to base UOM {base_uom} "
                f"(f_po={_s(f_po)}, f_inv={_s(f_inv)}).",
                po_value=po_line.uom, invoice_value=inv_line.uom,
                evidence_ids=[f"{pv}.uom", f"{iv}.uom"]
                + ([f"ref.uom.{po_line.product_id}"] if f_po != 1 else []))
    )

    q_po = po_line.ordered_qty * f_po
    q_inv = inv_line.invoiced_qty * f_inv
    line.normalized_po_qty = q_po
    line.normalized_invoice_qty = q_inv
    line.po_value_normalized = {"quantity": _s(q_po), "uom": base_uom}
    line.invoice_value_normalized = {"quantity": _s(q_inv), "uom": base_uom}

    _quantity_check(po_line, q_po, q_inv, base_uom, policy, prior_allocated,
                    run, line, allocations, pv, iv)

    # Price basis (b) and price checks
    amount = provided_amount
    basis_known = po_line.price_basis is not None and inv_line.price_basis is not None
    if not basis_known:
        missing = [n for n, ln in (("PO", po_line), ("invoice", inv_line))
                   if ln.price_basis is None]
        findings.append(
            Finding(finding_id=run.next_id(), check="price_basis", status=NOT_EVALUATED,
                    result_state=FindingStatus.INSUFFICIENT_EVIDENCE,
                    message=f"Price basis missing on {' and '.join(missing)} line; "
                    "price comparison blocked.",
                    evidence_ids=[f"{pv}.unit_price", f"{iv}.unit_price"])
        )
        for check in PRICE_CHECKS:
            findings.append(_blocked(run, check, "price basis unknown"))
    elif not currency_ok:
        for check in PRICE_CHECKS:
            findings.append(_blocked(run, check, "currency mismatch held"))
    else:
        p_po = po_line.unit_price.amount / (po_line.price_basis * f_po)
        p_inv = inv_line.unit_price.amount / (inv_line.price_basis * f_inv)
        line.po_unit_price_base = Money(amount=p_po)
        line.invoice_unit_price_base = Money(amount=p_inv)
        line.po_value_normalized["unit_price"] = _s(p_po)
        line.invoice_value_normalized["unit_price"] = _s(p_inv)
        amount = _price_checks(inv_line, p_po, p_inv, q_inv, base_uom, policy,
                               run, line, pv, iv)

    return _finish(line, run), amount


def _blocked(run: _Run, check: str, reason: str) -> Finding:
    return Finding(finding_id=run.next_id(), check=check, status=NOT_EVALUATED,
                   result_state=None, message=f"{check} not evaluated: {reason}.")


def _contract_display(
    po_line: POLine,
    po: PurchaseOrder,
    price_ref: list[PriceContract],
    run: _Run,
    line: LineResult,
) -> None:
    """Contract price is evidence only; it never overwrites the PO price."""
    try:
        contract = get_contract_price(po_line.product_id, po.order_date, price_ref,
                                      po.supplier_id, po.currency)
    except ReconciliationError as exc:
        key = f"ref.contract_conflict.{po_line.product_id}"
        run.add(key, "reference:price_contracts", "contract_conflict", str(exc))
        line.findings.append(
            Finding(finding_id=run.next_id(), check="contract_reference", status=NOT_EVALUATED,
                    result_state=None,
                    message=f"Contradictory contract evidence flagged: {exc}",
                    evidence_ids=[key])
        )
        return
    if contract is None:
        return
    per_unit = contract.agreed_price.amount / contract.price_basis
    line.contract_price_base = Money(amount=per_unit)
    key = f"contract.{contract.contract_id}"
    run.add(key, contract.contract_id, "agreed_price", str(contract.agreed_price),
            f"{_s(per_unit)} {contract.currency} per {po_line.uom}, effective "
            f"{contract.effective_from} to {contract.effective_to or 'open'}")
    line.findings.append(
        Finding(finding_id=run.next_id(), check="contract_reference", status=PASSED,
                result_state=None,
                message=f"Contract {contract.contract_id} price {_s(per_unit)} per "
                f"{po_line.uom} shown separately; it does not override the PO price.",
                evidence_ids=[key])
    )


def _quantity_check(
    po_line: POLine,
    q_po: Decimal,
    q_inv: Decimal,
    base_uom: str,
    policy: ReconciliationPolicy,
    prior_allocated: dict[str, Decimal],
    run: _Run,
    line: LineResult,
    allocations: list[dict[str, str]],
    pv: str,
    iv: str,
) -> None:
    prior = prior_allocated.get(po_line.line_id, ZERO)
    cumulative = prior + q_inv
    over = cumulative - q_po
    ev = [f"{pv}.quantity", f"{iv}.quantity"]
    line.delta["quantity"] = _signed(q_inv - q_po)
    line.delta_qty = q_inv - q_po
    common = dict(po_value=_s(q_po), invoice_value=_s(q_inv), normalized_po_value=_s(q_po),
                  normalized_invoice_value=_s(cumulative),
                  tolerance_applied=_s(policy.qty_tolerance), evidence_ids=ev)
    if over > policy.qty_tolerance:
        line.findings.append(
            Finding(finding_id=run.next_id(), check="quantity", status=FAILED,
                    result_state=FindingStatus.QUANTITY_VARIANCE,
                    message=f"Cumulative invoiced {_s(cumulative)} {base_uom} (prior "
                    f"{_s(prior)} + this {_s(q_inv)}) exceeds PO authorization {_s(q_po)} "
                    f"by {_s(over)}.",
                    delta=_signed(over), **common)
        )
        line.delta["cumulative_over"] = _signed(over)
        return
    prior_allocated[po_line.line_id] = cumulative
    line.allocated_qty = q_inv
    allocations.append({"po_line": po_line.line_id, "quantity": _s(q_inv), "uom": base_uom})
    balance = q_po - cumulative
    if balance > 0:
        line.findings.append(
            Finding(finding_id=run.next_id(), check="quantity", status=PASSED,
                    result_state=FindingStatus.PARTIAL_BILLING,
                    message=f"Partial billing: {_s(q_inv)} {base_uom} allocated; open PO "
                    f"balance {_s(balance)} {base_uom}. Not a shortage.",
                    delta=_signed(cumulative - q_po), **common)
        )
        line.delta["po_balance"] = _s(balance)
    else:
        line.findings.append(
            Finding(finding_id=run.next_id(), check="quantity", status=PASSED,
                    result_state=None,
                    message=f"Quantity {_s(q_inv)} {base_uom} within PO authorization "
                    f"{_s(q_po)}.",
                    delta=_signed(cumulative - q_po), **common)
        )


def _price_checks(
    inv_line: InvoiceLine,
    p_po: Decimal,
    p_inv: Decimal,
    q_inv: Decimal,
    base_uom: str,
    policy: ReconciliationPolicy,
    run: _Run,
    line: LineResult,
    pv: str,
    iv: str,
) -> Decimal:
    delta = p_inv - p_po
    tolerance = max(policy.price_abs_tolerance.amount, abs(p_po) * policy.price_rel_tolerance)
    ok = abs(delta) <= tolerance
    line.delta["unit_price"] = _signed(delta)
    line.delta_price = Money(amount=delta)
    line.findings.append(
        Finding(finding_id=run.next_id(), check="unit_price",
                status=PASSED if ok else FAILED,
                result_state=None if ok else FindingStatus.PRICE_VARIANCE,
                message=f"Normalized unit price PO {_s(p_po)} vs invoice {_s(p_inv)} per "
                f"{base_uom}; delta {_signed(delta)}, tolerance {_s(tolerance)}"
                + (" (within tolerance)." if ok else " (exceeds tolerance)."),
                po_value=_s(p_po), invoice_value=_s(p_inv),
                normalized_po_value=_s(p_po), normalized_invoice_value=_s(p_inv),
                delta=_signed(delta),
                tolerance_applied=f"{_s(tolerance)} ({policy.policy_version})",
                evidence_ids=[f"{pv}.unit_price", f"{iv}.unit_price"])
    )

    expected_ext = round_money(q_inv * p_po)
    computed_inv_ext = round_money(q_inv * p_inv)
    provided = inv_line.extended_price.amount if inv_line.extended_price else None
    actual_ext = provided if provided is not None else computed_inv_ext
    ext_delta = actual_ext - expected_ext
    line.delta["extension"] = _signed_money(ext_delta)
    line.po_value_normalized["extension"] = str(expected_ext)
    line.invoice_value_normalized["extension"] = str(actual_ext)
    ext_ok = abs(ext_delta) <= round_money(q_inv * tolerance)
    line.findings.append(
        Finding(finding_id=run.next_id(), check="extension_vs_po",
                status=PASSED if ext_ok else FAILED,
                result_state=None if ext_ok else FindingStatus.PRICE_VARIANCE,
                message=f"Expected extension at PO price {expected_ext}; invoice extension "
                f"{actual_ext}; delta {_signed_money(ext_delta)}.",
                po_value=str(expected_ext), invoice_value=str(actual_ext),
                delta=_signed_money(ext_delta),
                evidence_ids=[f"{pv}.unit_price", f"{iv}.quantity"]
                + ([f"{iv}.extended_price"] if provided is not None else []))
    )

    if provided is None:
        line.findings.append(_blocked(run, "invoice_extension_arithmetic",
                                      "invoice did not provide an extension"))
        return computed_inv_ext
    arith_ok = provided == computed_inv_ext
    line.findings.append(
        Finding(finding_id=run.next_id(), check="invoice_extension_arithmetic",
                status=PASSED if arith_ok else FAILED,
                result_state=None if arith_ok else FindingStatus.PRICE_VARIANCE,
                message=f"Invoice extension {provided} vs invoice qty x price "
                f"{computed_inv_ext}.",
                invoice_value=str(provided), normalized_invoice_value=str(computed_inv_ext),
                delta=_signed_money(provided - computed_inv_ext),
                evidence_ids=[f"{iv}.extended_price", f"{iv}.unit_price", f"{iv}.quantity"])
    )
    return provided


def _totals_check(invoice: Invoice, line_amounts: list[Decimal | None], run: _Run) -> Finding:
    if invoice.charges:
        kinds = ", ".join(sorted({str(c.get("type", "unknown")) for c in invoice.charges}))
        run.add("inv.charges", invoice.invoice_id, "charges", kinds)
        return Finding(finding_id=run.next_id(), check="invoice_total", status=NOT_EVALUATED,
                       result_state=FindingStatus.NOT_EVALUATED,
                       message=f"Unsupported header charges ({kinds}); invoice total not "
                       "evaluated and held for review. No silent totals pass.",
                       evidence_ids=["inv.charges"])
    if invoice.total_amount is None:
        return _blocked(run, "invoice_total", "invoice did not provide a total")
    total = invoice.total_amount.amount
    run.add("inv.total_amount", invoice.invoice_id, "total_amount", str(total))
    if any(a is None for a in line_amounts):
        return _blocked(run, "invoice_total", "a line amount is unknown")
    line_sum = round_money(sum(line_amounts, ZERO))  # type: ignore[arg-type]
    delta = total - line_sum
    ok = delta == 0
    return Finding(finding_id=run.next_id(), check="invoice_total",
                   status=PASSED if ok else FAILED,
                   result_state=None if ok else FindingStatus.PRICE_VARIANCE,
                   message=f"Invoice total {total} vs sum of lines {line_sum}"
                   + ("." if ok else f"; totals variance {_signed_money(delta)}."),
                   invoice_value=str(total), normalized_invoice_value=str(line_sum),
                   delta=_signed_money(delta), evidence_ids=["inv.total_amount"])
