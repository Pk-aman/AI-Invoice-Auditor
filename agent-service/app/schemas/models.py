"""Typed domain models shared by agents, persistence and API."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]
Recommendation = Literal["approve", "review", "reject"]
ValidationStatus = Literal["passed", "failed", "partial"]


class LineItem(BaseModel):
    line_number: int | None = None
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_id: str | None = None
    po_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    subtotal: Decimal | None = None
    tax_amount: Decimal | None = None
    tax_rate: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    extras: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """Internal (data-validation) finding: completeness / arithmetic / currency."""

    code: str
    field: str | None = None
    message: str
    severity: Severity = "medium"


class Discrepancy(BaseModel):
    """Invoice-vs-ERP mismatch; one row per mismatched field."""

    field_name: str
    invoice_value: str | None = None
    erp_value: str | None = None
    deviation_pct: Decimal | None = None
    severity: Severity = "medium"
    code: str = "mismatch"


class ErpVendor(BaseModel):
    vendor_id: str
    vendor_name: str
    country: str | None = None
    currency: str
    score: float | None = None


class ErpPoLine(BaseModel):
    line_number: int
    item_code: str
    description: str | None = None
    qty: Decimal
    unit_price: Decimal
    currency: str


class ErpPurchaseOrder(BaseModel):
    po_number: str
    vendor_id: str
    line_items: list[ErpPoLine] = Field(default_factory=list)


class ErpSku(BaseModel):
    item_code: str
    category: str | None = None
    uom: str | None = None
    gst_rate: Decimal | None = None


class BusinessResult(BaseModel):
    discrepancies: list[Discrepancy] = Field(default_factory=list)
    vendor_id: str | None = None
    po_number: str | None = None
    vendor_found: bool = False
    po_inferred: bool = False
    notes: list[str] = Field(default_factory=list)
