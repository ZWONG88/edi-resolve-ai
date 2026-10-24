"""Deterministic two-way rules engine (PRD sections 18-19). No AI, no floats, no file I/O.

Normalization: Q_base = Q_raw * f, P_base = P_raw / (b * f), where f is verified base
units per transaction UOM and b is the number of transaction UOMs the price covers.
Money is rounded with ROUND_HALF_UP only at line extension.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from backend.exceptions import ReconciliationError
from backend.models import (
    STATE_PRECEDENCE,
    CaseResult,
    CheckStatus,
    DiscrepancyRecord,
    Finding,
    Invoice,
    LineItem,
    PriceContract,
    PurchaseOrder,
    ResultState,
    TolerancePolicy,
    UOMConversion,
)
from backend.reference import get_contract_price, get_conversion

CENT = Decimal("0.01")
ZERO = Decimal("0")

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


def case_state(states: list[ResultState | None]) -> ResultState:
    present = {s for s in states if s is not None}
    for state in STATE_PRECEDENCE:
        if state in present:
            return state
    return ResultState.MATCHED_2WAY


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"F{self.n:03d}"


def _link_line(inv_line: LineItem, po: PurchaseOrder) -> tuple[list[LineItem], str]:
    """Return candidate PO lines and how they were found. Never guesses."""
    ref = inv_line.raw.fields.get("po_line_ref", "").strip()
    if ref:
        exact = [pl for pl in po.lines if pl.line_id == ref]
        if exact:
            return exact, "po_line_ref"
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
    uom_ref: dict[str, list[UOMConversion]],
    on_date: str,
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
    uom_ref: dict[str, list[UOMConversion]],
    price_ref: dict[str, list[PriceContract]],
    policy: TolerancePolicy | None = None,
    prior_allocated: dict[str, Decimal] | None = None,
    case_id: str = "pending",
) -> CaseResult:
    """Run every applicable P0 check. Multiple findings may coexist."""
    policy = policy or TolerancePolicy()
    prior_allocated = dict(prior_allocated or {})
    ids = _Ids()
    evidence: dict[str, str] = {
        "po.number": po.po_number,
        "po.date": po.po_date,
        "po.currency": po.currency,
        "inv.number": invoice.invoice_number,
        "inv.currency": invoice.currency,
        "policy.version": policy.version,
    }
    header: list[Finding] = []
    records: list[DiscrepancyRecord] = []
    allocations: list[dict[str, str]] = []

    currency_ok = po.currency == invoice.currency
    header.append(
        Finding(
            ids.next(),
            "currency",
            CheckStatus.PASSED if currency_ok else CheckStatus.NOT_EVALUATED,
            None if currency_ok else ResultState.INSUFFICIENT_EVIDENCE,
            "Currencies match."
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
        rec, amount = _compare_line(
            inv_line, po, uom_ref, price_ref, policy, prior_allocated,
            currency_ok, ids, evidence, allocations, case_id,
        )
        records.append(rec)
        line_amounts.append(amount)

    header.append(_totals_check(invoice, line_amounts, ids, evidence))
    header.append(
        Finding(
            ids.next(),
            "receipt",
            CheckStatus.NOT_EVALUATED,
            None,
            "Receipt (861) verification not evaluated: two-way P0 mode.",
        )
    )

    states = [f.result_state for f in header] + [r.result_state for r in records]
    return CaseResult(
        result_state=case_state(states),
        records=records,
        header_findings=header,
        evidence=evidence,
        policy_version=policy.version,
        allocations=allocations,
    )


def _compare_line(
    inv_line: LineItem,
    po: PurchaseOrder,
    uom_ref: dict[str, list[UOMConversion]],
    price_ref: dict[str, list[PriceContract]],
    policy: TolerancePolicy,
    prior_allocated: dict[str, Decimal],
    currency_ok: bool,
    ids: _Ids,
    evidence: dict[str, str],
    allocations: list[dict[str, str]],
    case_id: str,
) -> tuple[DiscrepancyRecord, Decimal | None]:
    iv = f"inv.{inv_line.line_id}"
    for key, value in inv_line.raw.fields.items():
        if key != "description":  # description is untrusted free text
            evidence[f"{iv}.{key}"] = value
    rec = DiscrepancyRecord(
        case_id=case_id,
        po_line=None,
        invoice_line=inv_line.line_id,
        result_state=ResultState.MATCHED_2WAY,
        invoice_value_raw=dict(inv_line.raw.fields),
        tolerance_applied=policy.version,
    )
    findings = rec.findings
    provided_amount = inv_line.extended_price

    candidates, how = _link_line(inv_line, po)
    if len(candidates) != 1:
        msg = (
            f"No PO line matches product {inv_line.product_id} / UPC {inv_line.upc or '-'}."
            if not candidates
            else f"{len(candidates)} PO lines match by {how} "
            f"({', '.join(c.line_id for c in candidates)}); no unique line reference."
        )
        findings.append(
            Finding(ids.next(), "line_link", CheckStatus.NOT_EVALUATED,
                    ResultState.REVIEW_LINK, msg, evidence_ids=[f"{iv}.product_id"])
        )
        for check in ("quantity", *PRICE_CHECKS):
            findings.append(_blocked(ids, check, "line link is not unique"))
        rec.not_evaluated_checks = [f.check for f in findings]
        rec.result_state = case_state([f.result_state for f in findings])
        return rec, provided_amount

    po_line = candidates[0]
    pv = f"po.{po_line.line_id}"
    for key, value in po_line.raw.fields.items():
        if key != "description":
            evidence[f"{pv}.{key}"] = value
    rec.po_line = po_line.line_id
    rec.po_value_raw = dict(po_line.raw.fields)
    findings.append(
        Finding(ids.next(), "line_link", CheckStatus.PASSED, None,
                f"Linked to PO line {po_line.line_id} by {how}.",
                evidence_ids=[f"{pv}.line_id", f"{iv}.line_id"])
    )

    _contract_display(po_line, po.po_date, price_ref, ids, evidence, findings)

    # UOM normalization (f)
    try:
        factors = _normalize_factors(
            po_line.product_id, po_line.uom, inv_line.uom, uom_ref, po.po_date
        )
        conflict = None
    except ReconciliationError as exc:
        factors, conflict = None, str(exc)
    if factors is None:
        findings.append(
            Finding(
                ids.next(), "uom_conversion", CheckStatus.NOT_EVALUATED,
                ResultState.INSUFFICIENT_EVIDENCE,
                conflict
                or f"No verified {po_line.uom}->{inv_line.uom} conversion for "
                f"{po_line.product_id}; no factor guessed.",
                po_value=po_line.uom, invoice_value=inv_line.uom,
                evidence_ids=[f"{pv}.uom", f"{iv}.uom"],
            )
        )
        for check in ("quantity", *PRICE_CHECKS):
            findings.append(_blocked(ids, check, "UOM conversion missing or conflicting"))
        rec.not_evaluated_checks = [f.check for f in findings
                                    if f.status == CheckStatus.NOT_EVALUATED]
        rec.result_state = case_state([f.result_state for f in findings])
        return rec, provided_amount

    f_po, f_inv, base_uom = factors
    if f_po != 1:
        evidence[f"ref.uom.{po_line.product_id}"] = f"1 {po_line.uom} = {_s(f_po)} {base_uom}"
    findings.append(
        Finding(ids.next(), "uom_conversion", CheckStatus.PASSED, None,
                f"Normalized to base UOM {base_uom} (f_po={_s(f_po)}, f_inv={_s(f_inv)}).",
                po_value=po_line.uom, invoice_value=inv_line.uom,
                evidence_ids=[f"{pv}.uom", f"{iv}.uom"]
                + ([f"ref.uom.{po_line.product_id}"] if f_po != 1 else []))
    )

    q_po = po_line.quantity * f_po
    q_inv = inv_line.quantity * f_inv
    rec.po_value_normalized = {"quantity": _s(q_po), "uom": base_uom}
    rec.invoice_value_normalized = {"quantity": _s(q_inv), "uom": base_uom}

    _quantity_check(po_line, inv_line, q_po, q_inv, base_uom, policy, prior_allocated,
                    ids, findings, rec, allocations, pv, iv)

    # Price basis (b) and price checks
    amount = provided_amount
    basis_known = po_line.price_basis_qty is not None and inv_line.price_basis_qty is not None
    if not basis_known:
        missing = [n for n, ln in (("PO", po_line), ("invoice", inv_line))
                   if ln.price_basis_qty is None]
        findings.append(
            Finding(ids.next(), "price_basis", CheckStatus.NOT_EVALUATED,
                    ResultState.INSUFFICIENT_EVIDENCE,
                    f"Price basis missing on {' and '.join(missing)} line; "
                    "price comparison blocked.",
                    evidence_ids=[f"{pv}.unit_price", f"{iv}.unit_price"])
        )
        for check in PRICE_CHECKS:
            findings.append(_blocked(ids, check, "price basis unknown"))
    elif not currency_ok:
        for check in PRICE_CHECKS:
            findings.append(_blocked(ids, check, "currency mismatch held"))
    else:
        p_po = po_line.unit_price / (po_line.price_basis_qty * f_po)
        p_inv = inv_line.unit_price / (inv_line.price_basis_qty * f_inv)
        rec.po_value_normalized["unit_price"] = _s(p_po)
        rec.invoice_value_normalized["unit_price"] = _s(p_inv)
        amount = _price_checks(inv_line, p_po, p_inv, q_inv, base_uom, policy,
                               ids, findings, rec, pv, iv)

    rec.not_evaluated_checks = [f.check for f in findings
                                if f.status == CheckStatus.NOT_EVALUATED]
    rec.result_state = case_state([f.result_state for f in findings])
    return rec, amount


def _blocked(ids: _Ids, check: str, reason: str) -> Finding:
    return Finding(ids.next(), check, CheckStatus.NOT_EVALUATED, None,
                   f"{check} not evaluated: {reason}.")


def _contract_display(
    po_line: LineItem,
    po_date: str,
    price_ref: dict[str, list[PriceContract]],
    ids: _Ids,
    evidence: dict[str, str],
    findings: list[Finding],
) -> None:
    """Contract price is evidence only; it never overwrites the PO price."""
    try:
        contract = get_contract_price(po_line.product_id, po_date, price_ref)
    except ReconciliationError as exc:
        key = f"ref.contract_conflict.{po_line.product_id}"
        evidence[key] = str(exc)
        findings.append(
            Finding(ids.next(), "contract_reference", CheckStatus.NOT_EVALUATED, None,
                    f"Contradictory contract evidence flagged: {exc}", evidence_ids=[key])
        )
        return
    if contract is None:
        return
    key = f"contract.{contract.contract_id}"
    evidence[key] = (
        f"{_s(contract.unit_price)}/{contract.uom} effective {contract.effective_date}"
        f" to {contract.expiry_date or 'open'}"
    )
    findings.append(
        Finding(ids.next(), "contract_reference", CheckStatus.PASSED, None,
                f"Contract {contract.contract_id} price {_s(contract.unit_price)}/"
                f"{contract.uom} shown separately; it does not override the PO price.",
                evidence_ids=[key])
    )


def _quantity_check(
    po_line: LineItem,
    inv_line: LineItem,
    q_po: Decimal,
    q_inv: Decimal,
    base_uom: str,
    policy: TolerancePolicy,
    prior_allocated: dict[str, Decimal],
    ids: _Ids,
    findings: list[Finding],
    rec: DiscrepancyRecord,
    allocations: list[dict[str, str]],
    pv: str,
    iv: str,
) -> None:
    prior = prior_allocated.get(po_line.line_id, ZERO)
    cumulative = prior + q_inv
    over = cumulative - q_po
    ev = [f"{pv}.quantity", f"{iv}.quantity"]
    rec.delta["quantity"] = _signed(q_inv - q_po)
    if over > policy.quantity:
        findings.append(
            Finding(ids.next(), "quantity", CheckStatus.FAILED, ResultState.QUANTITY_VARIANCE,
                    f"Cumulative invoiced {_s(cumulative)} {base_uom} (prior {_s(prior)} + "
                    f"this {_s(q_inv)}) exceeds PO authorization {_s(q_po)} by {_s(over)}.",
                    po_value=_s(q_po), invoice_value=_s(q_inv),
                    normalized_po_value=_s(q_po), normalized_invoice_value=_s(cumulative),
                    delta=_signed(over), tolerance_applied=_s(policy.quantity),
                    evidence_ids=ev)
        )
        rec.delta["cumulative_over"] = _signed(over)
        return
    prior_allocated[po_line.line_id] = cumulative
    allocations.append({"po_line": po_line.line_id, "quantity": _s(q_inv), "uom": base_uom})
    balance = q_po - cumulative
    if balance > 0:
        findings.append(
            Finding(ids.next(), "quantity", CheckStatus.PASSED, ResultState.PARTIAL_BILLING,
                    f"Partial billing: {_s(q_inv)} {base_uom} allocated; open PO balance "
                    f"{_s(balance)} {base_uom}. Not a shortage.",
                    po_value=_s(q_po), invoice_value=_s(q_inv),
                    normalized_po_value=_s(q_po), normalized_invoice_value=_s(cumulative),
                    delta=_signed(cumulative - q_po), tolerance_applied=_s(policy.quantity),
                    evidence_ids=ev)
        )
        rec.delta["po_balance"] = _s(balance)
    else:
        findings.append(
            Finding(ids.next(), "quantity", CheckStatus.PASSED, None,
                    f"Quantity {_s(q_inv)} {base_uom} within PO authorization {_s(q_po)}.",
                    po_value=_s(q_po), invoice_value=_s(q_inv),
                    normalized_po_value=_s(q_po), normalized_invoice_value=_s(cumulative),
                    delta=_signed(cumulative - q_po), tolerance_applied=_s(policy.quantity),
                    evidence_ids=ev)
        )


def _price_checks(
    inv_line: LineItem,
    p_po: Decimal,
    p_inv: Decimal,
    q_inv: Decimal,
    base_uom: str,
    policy: TolerancePolicy,
    ids: _Ids,
    findings: list[Finding],
    rec: DiscrepancyRecord,
    pv: str,
    iv: str,
) -> Decimal:
    delta = p_inv - p_po
    tolerance = max(policy.price_abs, abs(p_po) * policy.price_rel)
    ok = abs(delta) <= tolerance
    rec.delta["unit_price"] = _signed(delta)
    findings.append(
        Finding(ids.next(), "unit_price",
                CheckStatus.PASSED if ok else CheckStatus.FAILED,
                None if ok else ResultState.PRICE_VARIANCE,
                f"Normalized unit price PO {_s(p_po)} vs invoice {_s(p_inv)} per {base_uom}; "
                f"delta {_signed(delta)}, tolerance {_s(tolerance)}"
                + (" (within tolerance)." if ok else " (exceeds tolerance)."),
                po_value=_s(p_po), invoice_value=_s(p_inv),
                normalized_po_value=_s(p_po), normalized_invoice_value=_s(p_inv),
                delta=_signed(delta), tolerance_applied=f"{_s(tolerance)} ({policy.version})",
                evidence_ids=[f"{pv}.unit_price", f"{iv}.unit_price"])
    )

    expected_ext = round_money(q_inv * p_po)
    computed_inv_ext = round_money(q_inv * p_inv)
    provided = inv_line.extended_price
    actual_ext = provided if provided is not None else computed_inv_ext
    ext_delta = actual_ext - expected_ext
    rec.delta["extension"] = _signed_money(ext_delta)
    rec.po_value_normalized["extension"] = str(expected_ext)
    rec.invoice_value_normalized["extension"] = str(actual_ext)
    ext_ok = abs(ext_delta) <= round_money(q_inv * tolerance)
    findings.append(
        Finding(ids.next(), "extension_vs_po",
                CheckStatus.PASSED if ext_ok else CheckStatus.FAILED,
                None if ext_ok else ResultState.PRICE_VARIANCE,
                f"Expected extension at PO price {expected_ext}; invoice extension "
                f"{actual_ext}; delta {_signed_money(ext_delta)}.",
                po_value=str(expected_ext), invoice_value=str(actual_ext),
                delta=_signed_money(ext_delta),
                evidence_ids=[f"{pv}.unit_price", f"{iv}.quantity"]
                + ([f"{iv}.extended_price"] if provided is not None else []))
    )

    if provided is None:
        findings.append(_blocked(ids, "invoice_extension_arithmetic",
                                 "invoice did not provide an extension"))
        return computed_inv_ext
    arith_ok = provided == computed_inv_ext
    findings.append(
        Finding(ids.next(), "invoice_extension_arithmetic",
                CheckStatus.PASSED if arith_ok else CheckStatus.FAILED,
                None if arith_ok else ResultState.PRICE_VARIANCE,
                f"Invoice extension {provided} vs invoice qty x price {computed_inv_ext}.",
                invoice_value=str(provided), normalized_invoice_value=str(computed_inv_ext),
                delta=_signed_money(provided - computed_inv_ext),
                evidence_ids=[f"{iv}.extended_price", f"{iv}.unit_price", f"{iv}.quantity"])
    )
    return provided


def _totals_check(
    invoice: Invoice,
    line_amounts: list[Decimal | None],
    ids: _Ids,
    evidence: dict[str, str],
) -> Finding:
    if invoice.charges:
        kinds = ", ".join(sorted({str(c.get("type", "unknown")) for c in invoice.charges}))
        evidence["inv.charges"] = kinds
        return Finding(ids.next(), "invoice_total", CheckStatus.NOT_EVALUATED,
                       ResultState.NOT_EVALUATED,
                       f"Unsupported header charges ({kinds}); invoice total not evaluated "
                       "and held for review. No silent totals pass.",
                       evidence_ids=["inv.charges"])
    if invoice.total_amount is None:
        return _blocked(ids, "invoice_total", "invoice did not provide a total")
    evidence["inv.total_amount"] = str(invoice.total_amount)
    if any(a is None for a in line_amounts):
        return _blocked(ids, "invoice_total", "a line amount is unknown")
    line_sum = round_money(sum(line_amounts, ZERO))  # type: ignore[arg-type]
    delta = invoice.total_amount - line_sum
    ok = delta == 0
    return Finding(ids.next(), "invoice_total",
                   CheckStatus.PASSED if ok else CheckStatus.FAILED,
                   None if ok else ResultState.PRICE_VARIANCE,
                   f"Invoice total {invoice.total_amount} vs sum of lines {line_sum}"
                   + ("." if ok else f"; totals variance {_signed_money(delta)}."),
                   po_value=None, invoice_value=str(invoice.total_amount),
                   normalized_invoice_value=str(line_sum), delta=_signed_money(delta),
                   evidence_ids=["inv.total_amount"])
