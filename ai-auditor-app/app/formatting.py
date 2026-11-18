"""Display helpers: money, dates, status labels, reasons. Pure functions (unit-tested)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app import ui_text as T
from app.models import Discrepancy, Invoice


def money(amount: Decimal | float | int | None, currency: str | None = None) -> str:
    if amount is None:
        return "—"
    text = f"{Decimal(str(amount)):,.2f}"
    return f"{text} {currency}" if currency else text


def pretty_date(d: date | datetime | None) -> str:
    if d is None:
        return "—"
    return d.strftime("%d %b %Y")


def pretty_datetime(d: datetime | None) -> str:
    return "—" if d is None else d.strftime("%d %b %Y, %H:%M")


def percent(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def outcome(inv: Invoice) -> tuple[str, str, str]:
    """(label, icon, colour) of the single status shown for an invoice."""
    if inv.recommendation in T.RECOMMENDATION:
        return T.RECOMMENDATION[inv.recommendation]
    if inv.validation_status is None and inv.recommendation is None:
        return T.PROCESSING
    return T.UNKNOWN


def outcome_text(inv: Invoice) -> str:
    label, icon, _ = outcome(inv)
    return f"{icon} {label}"


def checks_text(status: str | None) -> str:
    label, icon, _ = T.VALIDATION.get(status or "", T.UNKNOWN)
    return f"{icon} {label}"


def severity_text(sev: str | None) -> str:
    label, icon = T.SEVERITY.get((sev or "").lower(), (sev or "—", "⚪"))
    return f"{icon} {label}"


def field_label(name: str) -> str:
    if name in T.FIELD_LABELS:
        return T.FIELD_LABELS[name]
    parts = name.split(":")
    if len(parts) == 3 and parts[0] == "line":
        return f"Line {parts[1]} – {parts[2].replace('_', ' ')}"
    return name.replace("_", " ").capitalize()


def reason(inv: Invoice, discrepancies: list[Discrepancy]) -> str:
    """Plain-English reason for the recommendation (derived from current-round findings)."""
    if inv.recommendation is None:
        return T.REASON_PROCESSING
    if inv.recommendation == "approve":
        return T.REASON_APPROVE
    current = [d for d in discrepancies if not d.superseded]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    current.sort(key=lambda d: order.get((d.severity or "").lower(), 9))
    why = "; ".join(
        f"{field_label(d.field_name).lower()} (invoice: {d.invoice_value or 'missing'}, "
        f"ERP: {d.erp_value or 'none'})" for d in current[:3]
    ) or T.REASON_NONE
    if len(current) > 3:
        why += f"; and {len(current) - 3} more"
    template = T.REASON_REJECT if inv.recommendation == "reject" else T.REASON_REVIEW
    return template.format(why=why)
