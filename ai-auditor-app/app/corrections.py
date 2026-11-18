"""Pure logic for the corrections form: effective values, diffing and validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app import ui_text as T
from app.formatting import field_label
from app.models import Feedback, Invoice, LineItem

HEADER_FIELDS = ["invoice_number", "vendor_name", "po_number", "invoice_date", "currency",
                 "total_amount"]
LINE_FIELDS = ["item_code", "description", "quantity", "unit_price", "line_total"]
NUMERIC = {"total_amount", "quantity", "unit_price", "line_total"}


@dataclass(frozen=True)
class Change:
    field_name: str
    old: str
    new: str


def _s(v: object) -> str:
    return "" if v is None else str(v)


def line_field_name(line_number: int, column: str) -> str:
    return f"line:{line_number}:{column}"


def original_values(inv: Invoice, lines: list[LineItem]) -> dict[str, str]:
    vals = {f: _s(getattr(inv, f)) for f in HEADER_FIELDS}
    for i, li in enumerate(lines, start=1):
        n = li.line_number or i
        for col in LINE_FIELDS:
            vals[line_field_name(n, col)] = _s(getattr(li, col))
    return vals


def effective_values(inv: Invoice, lines: list[LineItem], history: list[Feedback]) -> dict[str, str]:
    """Extracted value overlaid with the most recent human correction per field."""
    vals = original_values(inv, lines)
    for fb in sorted(history, key=lambda f: f.id or 0):
        if fb.field_name in vals and fb.corrected_value is not None:
            vals[fb.field_name] = fb.corrected_value
    return vals


def _same(field_name: str, a: str, b: str) -> bool:
    a, b = a.strip(), b.strip()
    if a == b or (field_name == "currency" and a.upper() == b.upper()):
        return True
    if field_name.split(":")[-1] in NUMERIC:
        try:
            return Decimal(a) == Decimal(b)
        except InvalidOperation:
            return False
    return False


def diff(effective: dict[str, str], edited: dict[str, str]) -> list[Change]:
    out = []
    for name, new in edited.items():
        old = effective.get(name, "")
        if not _same(name, old, new):
            out.append(Change(name, old, new.strip()))
    return out


def validate(changes: list[Change]) -> list[str]:
    errors: list[str] = []
    for c in changes:
        label = field_label(c.field_name)
        col = c.field_name.split(":")[-1]
        if col in {"invoice_number", "vendor_name", "currency", "invoice_date", "total_amount",
                   "item_code"} and not c.new:
            errors.append(T.CORR_EMPTY.format(label=label))
        elif col == "invoice_date":
            try:
                date.fromisoformat(c.new)
            except ValueError:
                errors.append(T.CORR_BAD_DATE.format(label=label))
        elif col in NUMERIC:
            try:
                Decimal(c.new)
            except InvalidOperation:
                errors.append(T.CORR_BAD_NUMBER.format(label=label))
        elif col == "currency" and not (len(c.new) == 3 and c.new.isalpha()):
            errors.append(T.CORR_BAD_CURRENCY.format(label=label))
    # currency also needs shape check even though non-empty
    for c in changes:
        if c.field_name == "currency" and c.new and not (len(c.new) == 3 and c.new.isalpha()):
            msg = T.CORR_BAD_CURRENCY.format(label=field_label(c.field_name))
            if msg not in errors:
                errors.append(msg)
    return errors


def normalise(c: Change) -> Change:
    if c.field_name == "currency":
        return Change(c.field_name, c.old, c.new.upper())
    return c
