"""Invoice Data Validation agent: completeness + internal arithmetic against rules.yaml.

Deterministic first; the LLM is only used to turn findings into a plain-English summary.
"""
from __future__ import annotations

import json
import logging
from decimal import Decimal

from app import prompts
from app.core.llm import LLM
from app.core.normalize import jsonable
from app.core.rules import SATISFIED_BY, Rules
from app.schemas.models import ExtractedInvoice, Finding

log = logging.getLogger(__name__)

_CENT = Decimal("0.01")


def _present(inv: ExtractedInvoice, field: str) -> bool:
    for f in SATISFIED_BY.get(field, (field,)):
        v = getattr(inv, f, None)
        if v not in (None, ""):
            return True
    return False


def validate_data(inv: ExtractedInvoice, rules: Rules, *, arith_tol: float = 0.01) -> list[Finding]:
    tol = Decimal(str(arith_tol))
    out: list[Finding] = []

    # completeness: header
    for rule_name in rules.required_header:
        internal = rules.internal(rule_name)
        if not _present(inv, internal):
            out.append(Finding(code="missing_field", field=internal, severity="medium",
                               message=f"Required invoice field '{internal}' is missing"))
    # completeness: lines
    if not inv.line_items:
        out.append(Finding(code="no_line_items", field="line_items", severity="high", message="No line items were found"))
    for n, li in enumerate(inv.line_items, start=1):
        for rule_name in rules.required_line:
            internal = rules.internal(rule_name)
            if getattr(li, internal, None) in (None, ""):
                out.append(Finding(code="missing_field", field=f"line[{n}].{internal}", severity="medium",
                                   message=f"Line {n}: required field '{internal}' is missing"))

    # currency
    if inv.currency and inv.currency not in rules.accepted_currencies:
        out.append(Finding(code="invalid_currency", field="currency", severity="critical",
                           message=f"Currency {inv.currency} is not accepted (allowed: {', '.join(rules.accepted_currencies)})"))

    # line arithmetic: qty * unit_price = line_total
    for n, li in enumerate(inv.line_items, start=1):
        if None not in (li.quantity, li.unit_price, li.line_total):
            expected = li.quantity * li.unit_price
            if abs(expected - li.line_total) > max(tol, _CENT * (abs(expected) / 1000)):
                out.append(Finding(code="line_total_mismatch", field=f"line[{n}].line_total", severity="medium",
                                   message=f"Line {n}: {li.quantity} x {li.unit_price} = {expected}, but line total is {li.line_total}"))

    # totals: sum(lines) (+ tax) = total
    totals = [li.line_total for li in inv.line_items if li.line_total is not None]
    if totals:
        lines_sum = sum(totals, Decimal(0))
        if inv.subtotal is not None and abs(lines_sum - inv.subtotal) > tol:
            out.append(Finding(code="total_mismatch", field="subtotal", severity="high",
                               message=f"Line totals sum to {lines_sum} but the subtotal is {inv.subtotal}"))
        base = inv.subtotal if inv.subtotal is not None else lines_sum
        if inv.total_amount is not None:
            expected_total = base + (inv.tax_amount or Decimal(0))
            if inv.tax_amount is None and inv.subtotal is None:
                expected_total = lines_sum
            if abs(expected_total - inv.total_amount) > tol:
                out.append(Finding(code="total_mismatch", field="total_amount", severity="high",
                                   message=f"Expected total {expected_total} (lines{' + tax' if inv.tax_amount is not None else ''})"
                                           f" but the invoice total is {inv.total_amount}"))
    # tax arithmetic: tax = base * rate
    if inv.tax_amount is not None and inv.tax_rate is not None:
        base = inv.subtotal if inv.subtotal is not None else (sum(totals, Decimal(0)) if totals else None)
        if base:
            expected_tax = base * inv.tax_rate / Decimal(100)
            allowed = max(tol, abs(expected_tax) * Decimal(str(rules.tax_tol_pct)) / Decimal(100))
            if abs(expected_tax - inv.tax_amount) > allowed:
                out.append(Finding(code="tax_mismatch", field="tax_amount", severity="medium",
                                   message=f"Tax {inv.tax_amount} does not match {inv.tax_rate}% of {base} (expected {expected_tax:.2f})"))
    return out


def summarise_findings(inv: ExtractedInvoice, findings: list[Finding], llm: LLM) -> str:
    """Plain-English summary; falls back to a deterministic sentence if the LLM is unavailable."""
    if not findings:
        deterministic = "No data problems were found: required fields are present and the amounts add up."
    else:
        deterministic = "; ".join(f.message for f in findings[:6]) + ("." if findings else "")
    try:
        cfg = prompts.load("data_validation")["summarise"]
        user = prompts.render(cfg["user"], invoice_number=inv.invoice_number or "(no number)", vendor_name=inv.vendor_name or "(unknown vendor)",
                              findings=json.dumps(jsonable([f.model_dump() for f in findings]), ensure_ascii=False))
        return llm.complete("reasoning", prompts.persona() + "\n\n" + cfg["system"], user, max_tokens=300).strip()
    except Exception:  # noqa: BLE001 - summary is cosmetic, never fail validation for it
        log.warning("findings summary via LLM failed; using deterministic text", exc_info=True)
        return deterministic
