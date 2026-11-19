"""LangGraph state. Everything is JSON-safe. Until SQL Write #1 the extracted/translated text lives here; afterwards
the texts are read back from PostgreSQL and dropped from state. Parsed fields, validation output and reports stay in
the checkpoint (plus Qdrant/Redis) until finalization (SQL Write #2)."""
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
    content_checksum: str
    raw_text: str | None             # only between extraction and SQL Write #1
    translated_text: str | None      # only between translation and SQL Write #1
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
    report: dict                     # latest run's report
    original_report: dict            # run-1 report = the immutable ai_audit_report
    run_reports: dict[str, dict]     # str(run) -> report
    discrepancy_runs: dict[str, list[dict]]
    index_error: str | None
    failed: bool
    errors: list[str]
    # ---- human-in-the-loop ----
    status: str                      # NEEDS_REPORT_REVIEW | APPROVED | REJECTED | SKIPPED_MAX_RETRIES
    human_check_count: int
    review: dict                     # latest reviewer submission (resume value)
    force_finalize: bool             # ceiling reached: finalize SKIPPED_MAX_RETRIES without another pause
    feedback_log: list[dict]
    decision_log: list[dict]
    warnings: list[str]


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
    context_chunks: list[str]
    sources: list[dict]
    answer: str
