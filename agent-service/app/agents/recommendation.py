"""Deterministic recommendation policy (the LLM never decides). Evaluated top-down; first match wins."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.rules import ACTION_TO_RECOMMENDATION, Rules
from app.schemas.models import Discrepancy, Finding, Recommendation, ValidationStatus


@dataclass
class Outcome:
    status: ValidationStatus
    recommendation: Recommendation
    reasons: list[str] = field(default_factory=list)
    rule: int = 0  # which policy row decided (1-7), for the audit trail


def validation_status(findings: list[Finding], discrepancies: list[Discrepancy]) -> ValidationStatus:
    sev = [f.severity for f in findings] + [d.severity for d in discrepancies]
    if not sev:
        return "passed"
    return "partial" if all(s == "low" for s in sev) else "failed"


def recommend(findings: list[Finding], discrepancies: list[Discrepancy], translation_confidence: float | None, rules: Rules) -> Outcome:
    status = validation_status(findings, discrepancies)
    codes = {f.code for f in findings}

    # 1. currency not accepted
    if "invalid_currency" in codes:
        rec = ACTION_TO_RECOMMENDATION[rules.invalid_currency_action]
        msg = next(f.message for f in findings if f.code == "invalid_currency")
        return Outcome(status, rec, [msg], 1)  # type: ignore[arg-type]
    # 2. critical discrepancy (e.g. vendor unknown to the ERP)
    crit = [d for d in discrepancies if d.severity == "critical"]
    if crit:
        return Outcome(status, "reject", [f"{d.field_name}: '{d.invoice_value}' not found in the ERP" if d.code == "vendor_not_found"
                                          else f"critical discrepancy on {d.field_name}" for d in crit], 2)
    # 3. missing mandatory field
    missing = [f for f in findings if f.code in ("missing_field", "no_line_items")]
    if missing:
        return Outcome(status, ACTION_TO_RECOMMENDATION[rules.missing_field_action], [f.message for f in missing], 3)  # type: ignore[arg-type]
    # 4. totals do not add up
    mism = [f for f in findings if f.code == "total_mismatch"]
    if mism:
        return Outcome(status, ACTION_TO_RECOMMENDATION[rules.total_mismatch_action], [f.message for f in mism], 4)  # type: ignore[arg-type]
    # 5. any discrepancy beyond tolerance
    if discrepancies:
        return Outcome(status, "review", [f"{d.field_name}: invoice {d.invoice_value} vs ERP {d.erp_value}" for d in discrepancies], 5)
    # 6. any other data finding
    if findings:
        return Outcome(status, "review", [f.message for f in findings], 6)
    # 7. clean: approve only with high translation confidence
    conf = 1.0 if translation_confidence is None else translation_confidence
    if conf >= rules.auto_approve_confidence:
        return Outcome(status, "approve", ["No discrepancies and translation confidence is high"], 7)
    return Outcome(status, "review", [f"Translation confidence {conf:.2f} is below the auto-approve threshold {rules.auto_approve_confidence}"], 7)
