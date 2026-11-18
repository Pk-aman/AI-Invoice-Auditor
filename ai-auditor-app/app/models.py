"""View-models mirroring the backend DTOs.

ASSUMPTIONS (reconcile with the real OpenAPI of ai-auditor-services / agent-service):
see ASSUMPTIONS.md. Models ignore unknown fields and make most fields optional so that small
backend drift degrades gracefully instead of blanking the page.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Invoice(_M):
    invoice_id: str
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_id: str | None = None
    po_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None
    total_amount: Decimal | None = None
    validation_status: str | None = None  # passed | partial | failed (None while processing)
    recommendation: str | None = None  # approve | review | reject
    source_language: str | None = None
    translation_confidence: float | None = None
    ocr_confidence: float | None = None
    validation_run: int = 1
    extraction_method: str | None = None
    processed_at: datetime | None = None
    report_path: str | None = None


class InvoicePage(_M):
    items: list[Invoice] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25


class LineItem(_M):
    line_number: int | None = None
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None


class Discrepancy(_M):
    id: int | None = None
    validation_run: int = 1
    superseded: bool = False
    field_name: str
    invoice_value: str | None = None
    erp_value: str | None = None
    deviation_pct: Decimal | None = None
    severity: str | None = None  # low | medium | high | critical
    detected_at: datetime | None = None


class InvoiceText(_M):
    kind: str = "raw"
    text: str = ""


class ProcessingEvent(_M):
    stage: str
    state: str
    detail: dict[str, Any] | None = None
    created_at: datetime | None = None


class Feedback(_M):
    id: int | None = None
    field_name: str
    original_value: str | None = None
    corrected_value: str | None = None
    corrected_by: str | None = None
    corrected_at: datetime | None = None
    revalidated: bool = False


class Summary(_M):
    @model_validator(mode="before")
    @classmethod
    def _real_backend_keys(cls, d: Any) -> Any:
        # Real ai-auditor-services uses total_invoices / by_validation_status / flat counters.
        if isinstance(d, dict):
            d = dict(d)
            d.setdefault("total", d.get("total_invoices", 0))
            d.setdefault("by_status", d.get("by_validation_status", {}))
            d.setdefault("pending_review", d.get("needs_review", 0))
            rec = dict(d.get("by_recommendation") or {})
            for key, flat in (("approve", "approved"), ("review", "needs_review"),
                              ("reject", "rejected")):
                if key not in rec and flat in d:
                    rec[key] = d[flat]
            d["by_recommendation"] = rec
        return d

    total: int = 0
    by_recommendation: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    pending_review: int = 0
    pending_corrections: int = 0
    avg_translation_confidence: float | None = None


class VendorStat(_M):
    vendor: str
    invoices: int = 0
    discrepancies: int = 0


class DayStat(_M):
    day: date
    count: int = 0


class DiscrepancyTypeStat(_M):
    field_name: str
    count: int = 0


class UploadResult(_M):
    upload_id: str | None = None
    filename: str
    checksum: str | None = None
    status: str  # QUEUED | DUPLICATE | REJECTED | ...
    invoice_id: str | None = None
    detail: str | None = None
    reason: str | None = None  # real backend name for detail

    @model_validator(mode="after")
    def _merge_reason(self) -> "UploadResult":
        if self.detail is None and self.reason:
            self.detail = self.reason
        return self


class UploadStatus(_M):
    checksum: str | None = None
    status: str  # QUEUED | PROCESSING | COMPLETED | FAILED (real) or fine-grained stages (fake)
    stage: str | None = None
    invoice_id: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _use_stage_while_processing(self) -> "UploadStatus":
        # Real backend reports coarse status + pipeline stage; show the stage to the auditor.
        if self.status.upper() == "PROCESSING" and self.stage:
            self.status = self.stage
        return self


class RevalidateResponse(_M):
    validation_run: int | None = None


class PipelineStatus(_M):
    stage: str | None = None
    state: str  # QUEUED | RUNNING | COMPLETED | FAILED
    error: str | None = None


class Source(_M):
    invoice_id: str | None = None
    invoice_number: str | None = None
    source_type: str | None = None  # invoice | report
    chunk_text: str = ""


class RagAnswer(_M):
    answer: str
    route: str | None = None  # sql | rag
    sources: list[Source] = Field(default_factory=list)
    eval_id: str | None = None


class RagEvaluation(_M):
    state: str  # PENDING | READY | FAILED
    relevance: float | None = None
    groundedness: float | None = None
    context_relevance: float | None = None


class InvoiceFilters(_M):
    vendor: str | None = None
    status: str | None = None
    recommendation: str | None = None
    currency: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None

    def as_params(self) -> dict[str, str]:
        return {k: v.isoformat() if isinstance(v, date) else str(v)
                for k, v in self.model_dump().items() if v not in (None, "")}
