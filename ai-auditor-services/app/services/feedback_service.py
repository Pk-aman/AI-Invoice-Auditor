"""Feedback (human correction) validation and effective-value resolution.

A correction is always an INSERT. `original_value` is resolved server-side: the latest prior
correction for the same field if there is one, otherwise the extracted value.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import Connection, text

from app.core.errors import AppError
from app.repositories import feedback_repo, invoice_repo

HEADER_FIELDS = {
    "invoice_number": ("str", 64),
    "vendor_name": ("str", 255),
    "po_number": ("str", 64),
    "invoice_date": ("date", 0),
    "currency": ("currency", 3),
    "total_amount": ("money", 0),
}
LINE_FIELDS = {
    "item_code": ("str", 64),
    "description": ("str", 2000),
    "quantity": ("qty", 0),
    "unit_price": ("price", 0),
    "line_total": ("money", 0),
}
LINE_RE = re.compile(r"^line:(\d+):([a-z_]+)$")
_QUANT = {"money": Decimal("0.01"), "qty": Decimal("0.001"), "price": Decimal("0.0001")}


@dataclass(frozen=True)
class FieldSpec:
    field_name: str
    kind: str
    maxlen: int
    line_number: int | None
    column: str


def parse_field(field_name: str) -> FieldSpec:
    if field_name in HEADER_FIELDS:
        kind, maxlen = HEADER_FIELDS[field_name]
        return FieldSpec(field_name, kind, maxlen, None, field_name)
    m = LINE_RE.match(field_name)
    if m and m.group(2) in LINE_FIELDS and int(m.group(1)) >= 1:
        kind, maxlen = LINE_FIELDS[m.group(2)]
        return FieldSpec(field_name, kind, maxlen, int(m.group(1)), m.group(2))
    raise AppError(422, "unsupported_field", f"Field '{field_name}' cannot be corrected.",
                   {"supported": sorted(HEADER_FIELDS) + [f"line:<n>:{c}" for c in sorted(LINE_FIELDS)]})


def canonicalise(spec: FieldSpec, raw: str) -> str:
    v = raw.strip()
    if spec.kind in ("money", "qty", "price"):
        try:
            d = Decimal(v.replace(",", ""))
        except InvalidOperation:
            raise AppError(422, "invalid_value", f"'{raw}' is not a valid number for {spec.field_name}.") from None
        if not d.is_finite() or d < 0:
            raise AppError(422, "invalid_value", f"{spec.field_name} must be a non-negative number.")
        return str(d.quantize(_QUANT[spec.kind]))
    if spec.kind == "date":
        try:
            return date.fromisoformat(v).isoformat()
        except ValueError:
            raise AppError(422, "invalid_value", "Date must be in YYYY-MM-DD format.") from None
    if spec.kind == "currency":
        if not re.fullmatch(r"[A-Za-z]{3}", v):
            raise AppError(422, "invalid_value", "Currency must be a 3-letter ISO code, e.g. USD.")
        return v.upper()
    if not v:
        raise AppError(422, "invalid_value", f"{spec.field_name} cannot be empty.")
    if len(v) > spec.maxlen:
        raise AppError(422, "invalid_value", f"{spec.field_name} is longer than {spec.maxlen} characters.")
    return v


def _extracted_value(conn: Connection, invoice_id: uuid.UUID, spec: FieldSpec) -> str | None:
    if spec.line_number is None:
        row = conn.execute(
            text(f"SELECT {spec.column} AS v FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}
        ).mappings().first()
    else:
        row = conn.execute(
            text(f"SELECT {spec.column} AS v FROM audit.invoice_line_items WHERE invoice_id=:i AND line_number=:n "
                 "ORDER BY id LIMIT 1"),
            {"i": invoice_id, "n": spec.line_number},
        ).mappings().first()
        if row is None:
            raise AppError(404, "line_not_found", f"Invoice has no line {spec.line_number}.")
    return None if row is None or row["v"] is None else str(row["v"])


def effective_value(conn: Connection, invoice_id: uuid.UUID, spec: FieldSpec) -> str | None:
    found, val = feedback_repo.latest_correction(conn, invoice_id, spec.field_name)
    return val if found else _extracted_value(conn, invoice_id, spec)


def add_feedback(conn: Connection, invoice_id: uuid.UUID, field_name: str, corrected_value: str, by: str) -> dict:
    if not invoice_repo.invoice_exists(conn, invoice_id):
        raise AppError(404, "invoice_not_found", "Invoice not found.")
    spec = parse_field(field_name)
    corrected = canonicalise(spec, corrected_value)
    original = effective_value(conn, invoice_id, spec)
    return feedback_repo.insert_feedback(
        conn, invoice_id=invoice_id, field=spec.field_name, original=original, corrected=corrected, by=by.strip()
    )
