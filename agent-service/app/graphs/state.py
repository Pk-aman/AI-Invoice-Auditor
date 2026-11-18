"""LangGraph state. Only small, JSON-safe values live here (texts are read from PostgreSQL by each node),
so checkpoints stay compact and serialisable."""
from __future__ import annotations

from typing import Any, TypedDict


class InvoiceState(TypedDict, total=False):
    invoice_id: str
    file_path: str
    file_checksum: str
    file_type: str
    meta: dict
    duplicate: bool
    existing_invoice_id: str
    extraction_method: str
    ocr_confidence: float | None
    source_language: str
    translation_confidence: float | None
    invoice: dict[str, Any]          # ExtractedInvoice.model_dump(mode json)
    corrections: dict[str, str]      # applied human corrections (revalidation)
    data_findings: list[dict]
    data_summary: str
    discrepancies: list[dict]
    vendor_id: str | None
    po_number: str | None
    po_inferred: bool
    notes: list[str]
    validation_status: str
    recommendation: str
    reasons: list[str]
    validation_run: int
    report_path: str
    report: dict
    reindex_report_only: bool
    index_error: str | None
    failed: bool
    errors: list[str]


class RagState(TypedDict, total=False):
    question: str
    invoice_id: str | None
    vendor: str | None
    route: str                       # structured | open
    template: str | None
    params: dict
    rows: list[dict]
    hits: list[Any]
    context: str
    sources: list[dict]
    answer: str
