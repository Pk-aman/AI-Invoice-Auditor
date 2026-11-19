"""Human review: validate/normalise reviewer corrections against the CURRENT in-flight invoice and apply them.

Supported fields mirror the services API (`ai-auditor-services/.../feedback_service.py`): header fields and
`line:{n}:{item_code|description|quantity|unit_price|line_total}`. All checks happen before anything is persisted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.core.normalize import normalize_currency, parse_amount, parse_date
from app.schemas.models import ExtractedInvoice

log = logging.getLogger(__name__)

HEADER_FIELDS: dict[str, tuple[str, int]] = {
    "invoice_number": ("str", 64),
    "vendor_name": ("str", 255),
    "po_number": ("str", 64),
    "invoice_date": ("date", 0),
    "currency": ("currency", 3),
    "total_amount": ("money", 0),
}
LINE_FIELDS: dict[str, tuple[str, int]] = {
    "item_code": ("str", 64),
    "description": ("str", 2000),
    "quantity": ("qty", 0),
    "unit_price": ("price", 0),
    "line_total": ("money", 0),
}
_LINE_RE = re.compile(r"^line:(\d+):([a-z_]+)$")
_QUANT = {"money": Decimal("0.01"), "qty": Decimal("0.001"), "price": Decimal("0.0001")}


class ReviewError(Exception):
    """A reviewer-input problem; `status` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 422, code: str = "invalid_review"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


@dataclass(frozen=True)
class ResolvedCorrection:
    field_name: str
    original_value: str | None  # the current (server-side) value, never trusted from the client
    corrected_value: str


def _spec(field_name: str, inv: ExtractedInvoice) -> tuple[str, int, int | None, str]:
    """-> (kind, maxlen, line_index, attribute)"""
    if field_name in HEADER_FIELDS:
        kind, maxlen = HEADER_FIELDS[field_name]
        return kind, maxlen, None, field_name
    m = _LINE_RE.match(field_name)
    if m and m.group(2) in LINE_FIELDS and int(m.group(1)) >= 1:
        n = int(m.group(1))
        if n > len(inv.line_items):
            raise ReviewError(f"Invoice has no line {n} (it has {len(inv.line_items)}).", 422, "line_not_found")
        kind, maxlen = LINE_FIELDS[m.group(2)]
        return kind, maxlen, n - 1, m.group(2)
    raise ReviewError(
        f"Field '{field_name}' cannot be corrected.", 422, "unsupported_field")


def _canonical(kind: str, maxlen: int, field_name: str, raw: str) -> str:
    v = raw.strip()
    if kind in _QUANT:
        try:
            d = Decimal(v.replace(",", ""))
        except InvalidOperation:
            raise ReviewError(f"'{raw}' is not a valid number for {field_name}.", 422, "invalid_value") from None
        if not d.is_finite() or d < 0:
            raise ReviewError(f"{field_name} must be a non-negative number.", 422, "invalid_value")
        return str(d.quantize(_QUANT[kind]))
    if kind == "date":
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise ReviewError("Date must be in YYYY-MM-DD format.", 422, "invalid_value") from None
    if kind == "currency":
        if not re.fullmatch(r"[A-Za-z]{3}", v):
            raise ReviewError("Currency must be a 3-letter ISO code, e.g. USD.", 422, "invalid_value")
        return v.upper()
    if not v:
        raise ReviewError(f"{field_name} cannot be empty.", 422, "invalid_value")
    if len(v) > maxlen:
        raise ReviewError(f"{field_name} is longer than {maxlen} characters.", 422, "invalid_value")
    return v


def current_value(inv: ExtractedInvoice, field_name: str) -> str | None:
    kind, _, idx, attr = _spec(field_name, inv)
    target = inv if idx is None else inv.line_items[idx]
    value = getattr(target, attr)
    return None if value is None else (value.isoformat() if isinstance(value, date) else str(value))


def resolve_corrections(inv: ExtractedInvoice, corrections: list) -> list[ResolvedCorrection]:
    """Validate every correction (name, type, line existence, duplicates) and resolve its original value."""
    seen: set[str] = set()
    out: list[ResolvedCorrection] = []
    for c in corrections:
        kind, maxlen, _, _ = _spec(c.field_name, inv)
        if c.field_name in seen:
            raise ReviewError(f"Field '{c.field_name}' appears more than once.", 422, "duplicate_field")
        seen.add(c.field_name)
        out.append(ResolvedCorrection(c.field_name, current_value(inv, c.field_name),
                                      _canonical(kind, maxlen, c.field_name, c.corrected_value)))
    return out


def apply_corrections(inv: ExtractedInvoice, corrections: dict[str, str], symbol_map: dict[str, str]) -> tuple[ExtractedInvoice, dict[str, str]]:
    """Overlay corrections on a COPY of the invoice (in memory only; the AI report and original rows stay untouched)."""
    applied: dict[str, str] = {}
    inv = inv.model_copy(deep=True)
    for field_name, value in corrections.items():
        v = None if value is None or str(value).strip() == "" else str(value).strip()
        if field_name.startswith("line:"):
            try:
                _, n, attr = field_name.split(":", 2)
                li = inv.line_items[int(n) - 1]
            except (ValueError, IndexError):
                log.warning("ignoring correction for unknown line", extra={"field": field_name})
                continue
            if attr in ("quantity", "unit_price", "line_total"):
                setattr(li, attr, parse_amount(v))
            elif attr in ("item_code", "description"):
                setattr(li, attr, v)
            else:
                continue
        elif field_name in ("invoice_number", "vendor_name", "po_number"):
            setattr(inv, field_name, v)
            if field_name == "vendor_name":
                inv.vendor_id = None  # force re-resolution through the ERP
        elif field_name == "invoice_date":
            inv.invoice_date = parse_date(v)
        elif field_name == "currency":
            inv.currency = normalize_currency(v, symbol_map) or (v.upper() if v else None)
        elif field_name == "total_amount":
            inv.total_amount = parse_amount(v)
        else:
            log.warning("ignoring correction for unsupported field", extra={"field": field_name})
            continue
        applied[field_name] = value
    return inv, applied
