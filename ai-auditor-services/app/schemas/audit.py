import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class InvoiceSummary(BaseModel):
    invoice_id: uuid.UUID
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_id: str | None = None
    po_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None
    total_amount: float | None = None
    validation_status: str | None = None
    recommendation: str | None = None
    source_language: str | None = None
    translation_confidence: float | None = None
    validation_run: int = 1
    status: str = "INGESTED"          # INGESTED (awaiting/under review) | APPROVED | REJECTED | SKIPPED_MAX_RETRIES | LEGACY_FINALIZED
    human_check_count: int = 0
    processed_at: datetime | None = None


class InvoiceList(BaseModel):
    items: list[InvoiceSummary]
    total: int
    page: int
    page_size: int


class InvoiceDetail(InvoiceSummary):
    file_path: str | None = None
    file_checksum: str
    extraction_method: str | None = None
    ocr_confidence: float | None = None
    extracted_raw: dict[str, Any] | None = None
    report_available: bool = False
    ai_report_available: bool = False  # the immutable AI report is stored in PostgreSQL once the invoice is finalized
    content_checksum: str | None = None
    finalized_at: datetime | None = None
    pending_corrections: int = 0
    corrections: dict[str, str | None] = Field(
        default_factory=dict, description="latest corrected value per field (effective values)"
    )


class LineItem(BaseModel):
    id: int
    line_number: int | None = None
    item_code: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None


class Discrepancy(BaseModel):
    id: int
    validation_run: int
    superseded: bool
    field_name: str | None = None
    invoice_value: str | None = None
    erp_value: str | None = None
    deviation_pct: float | None = None
    severity: str | None = None
    detected_at: datetime | None = None


class InvoiceText(BaseModel):
    invoice_id: uuid.UUID
    kind: Literal["raw", "translated"]
    text: str | None = None


class ProcessingEvent(BaseModel):
    id: int
    invoice_id: uuid.UUID | None = None
    file_path: str | None = None
    file_checksum: str | None = None
    stage: str
    state: str
    detail: dict[str, Any] | None = None
    created_at: datetime | None = None


class FeedbackIn(BaseModel):
    field_name: str = Field(min_length=1, max_length=64)
    corrected_value: str = Field(max_length=2000)
    corrected_by: str = Field(min_length=1, max_length=128)


class Feedback(BaseModel):
    id: int
    invoice_id: uuid.UUID
    field_name: str
    original_value: str | None = None
    corrected_value: str | None = None
    corrected_by: str | None = None
    corrected_at: datetime | None = None
    revalidated: bool | None = None


class UploadResult(BaseModel):
    filename: str
    status: Literal["QUEUED", "DUPLICATE", "REJECTED"]
    checksum: str | None = None
    upload_id: str | None = None
    invoice_id: uuid.UUID | None = None
    reason: str | None = None


class ReviewDecision(BaseModel):
    id: int
    invoice_id: uuid.UUID
    review_round: int
    decision: str
    reviewer: str
    comments: str | None = None
    human_check_count: int
    decided_at: datetime | None = None


class UploadStatus(BaseModel):
    checksum: str
    invoice_id: uuid.UUID | None = None
    existing_invoice_id: uuid.UUID | None = None  # set when status is DUPLICATE
    status: Literal["QUEUED", "PROCESSING", "NEEDS_REVIEW", "COMPLETED", "FAILED", "DUPLICATE"]
    stage: str | None = None
    state: str | None = None
    error: str | None = None
    updated_at: datetime | None = None


class DocumentSummary(BaseModel):
    document_id: str
    checksum: str
    filename: str | None = None
    file_type: str = ""
    uploaded_at: datetime
    processing_status: str  # audited | review | processing | failed | awaiting | duplicate
    audit_result: str  # no_issues | needs_attention | approved | rejected | not_available
    invoice_id: uuid.UUID | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    recommendation: str | None = None
    status: str | None = None
    human_check_count: int | None = None
    duplicate_of: uuid.UUID | None = None
    error: str | None = None


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    page: int
    page_size: int


class DocumentStats(BaseModel):
    total: int
    processing: int
    awaiting_review: int = 0
    audited: int
    needs_attention: int
    awaiting: int
    failed: int


class Finding(BaseModel):
    severity: str
    message: str


class DocumentDetail(DocumentSummary):
    findings: list[Finding] = []
    reasons: list[str] = []
    summary: str | None = None
