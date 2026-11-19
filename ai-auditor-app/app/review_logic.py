"""Pure (Streamlit-free) helpers for the review dialog, so they are unit-testable.

The immutable AI report is NEVER edited here: corrections are field-level diffs against the invoice values the
agent currently holds, submitted through the review endpoint.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

HEADER_FIELDS = [
    ("invoice_number", "Invoice number"),
    ("vendor_name", "Vendor"),
    ("po_number", "Purchase order"),
    ("invoice_date", "Invoice date (YYYY-MM-DD)"),
    ("currency", "Currency (3 letters)"),
    ("total_amount", "Total amount"),
]
LINE_COLUMNS = [
    ("item_code", "Item code"),
    ("description", "Description"),
    ("quantity", "Quantity"),
    ("unit_price", "Unit price"),
    ("line_total", "Line total"),
]
NUMERIC = {"total_amount", "quantity", "unit_price", "line_total"}

STATUS_TEXT = {
    "NEEDS_REPORT_REVIEW": "Awaiting your review",
    "PROCESSING": "Still processing",
    "APPROVED": "Approved",
    "REJECTED": "Rejected",
    "SKIPPED_MAX_RETRIES": "Review limit reached",
    "LEGACY_FINALIZED": "Audited (before review was introduced)",
}
DECISION_LABEL = {"approve": "Approve", "reject": "Reject", "reject_and_revalidate": "Reject & revalidate"}


def _same(field: str, a: object, b: object) -> bool:
    """Compare a displayed value with the user's edit: numbers numerically, text after trimming."""
    sa, sb = ("" if a is None else str(a).strip()), ("" if b is None else str(b).strip())
    if sa == sb:
        return True
    if field in NUMERIC and sa and sb:
        try:
            return Decimal(sa.replace(",", "")) == Decimal(sb.replace(",", ""))
        except InvalidOperation:
            return False
    return False


def diff_corrections(invoice: dict, header: dict[str, str], lines: list[dict]) -> list[dict]:
    """Field corrections = the edits that differ from `invoice` (the agent's current values)."""
    out: list[dict] = []
    for field, _ in HEADER_FIELDS:
        if field in header and not _same(field, invoice.get(field), header[field]):
            out.append({"field_name": field, "original_value": _s(invoice.get(field)), "corrected_value": str(header[field]).strip()})
    originals = invoice.get("line_items") or []
    for idx, edited in enumerate(lines, start=1):
        if idx > len(originals):
            break
        for col, _ in LINE_COLUMNS:
            if col in edited and not _same(col, originals[idx - 1].get(col), edited[col]):
                out.append({"field_name": f"line:{idx}:{col}", "original_value": _s(originals[idx - 1].get(col)),
                            "corrected_value": _s(edited[col]) or ""})
    return out


def _s(v: object) -> str | None:
    return None if v is None or (isinstance(v, float) and v != v) else str(v).strip()


def validate_submission(decision: str, reviewer: str, corrections: list[dict]) -> str | None:
    """Client-side checks that mirror the API rules; the API remains the authority."""
    if not reviewer.strip():
        return "Enter your name so the review can be recorded."
    if decision == "reject_and_revalidate" and not corrections:
        return "Change at least one field before choosing Reject & revalidate, or use Reject."
    if decision == "approve" and corrections:
        return "Approving cannot include corrections. Use Reject & revalidate to apply your changes."
    return None


def checks_message(state: dict) -> tuple[str, str]:
    """(level, text) describing how many reviews are left; never promises more than the API allows."""
    count, maximum = int(state.get("human_check_count") or 0), int(state.get("max_human_checks") or 3)
    left = max(0, maximum - count)
    if not state.get("awaiting_review"):
        return "info", f"Reviews completed: {count}."
    if left > 0:
        return "info", f"Reviews completed: {count}. You can request up to {left} more re-validation(s)."
    return "warning", ("Reviews completed: %d. This is the last review: choosing Reject & revalidate will finalize the "
                       "invoice as 'review limit reached'." % count)


def response_notice(resp: dict) -> tuple[str, str]:
    """(level, message) for a successful review response."""
    status = resp.get("status")
    if status == "APPROVED":
        return "success", "Invoice approved and finalized."
    if status == "REJECTED":
        return "success", "Invoice rejected and finalized."
    if status == "SKIPPED_MAX_RETRIES":
        return "warning", ("Your changes were applied and re-validated, but the review limit was reached, so the invoice was "
                           f"finalized (review limit reached). {resp.get('discrepancies_count', 0)} issue(s) remain.")
    n = resp.get("discrepancies_count", 0)
    return "success", (f"Your corrections were saved and the invoice was re-validated (run {resp.get('validation_run')}). "
                       + ("No issues remain." if n == 0 else f"{n} issue(s) still need your attention."))
