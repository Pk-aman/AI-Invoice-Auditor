"""API request/response DTOs."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

_REVIEWER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._@'-]{0,127}$")


class ProcessRequest(BaseModel):
    file_path: str = Field(description="Path of a file under the incoming directory")


class ProcessResponse(BaseModel):
    status: str  # queued | duplicate
    invoice_id: str | None = None


class StatusResponse(BaseModel):
    invoice_id: str
    stage: str | None
    state: str | None
    error: str | None = None
    events: list[dict[str, Any]] = []


# ---- human-in-the-loop review ----------------------------------------------------------------------
class FieldCorrection(BaseModel):
    field_name: str = Field(min_length=1, max_length=64, description="invoice_number | vendor_name | po_number | invoice_date | currency | total_amount | line:<n>:<item_code|description|quantity|unit_price|line_total>")
    original_value: str | None = Field(default=None, max_length=2000, description="informational: the server records the CURRENT value, never this one")
    corrected_value: str = Field(max_length=2000)


class ReviewRequest(BaseModel):
    decision: Literal["approve", "reject_and_revalidate", "reject"]
    reviewer: str = Field(min_length=1, max_length=128)
    comments: str | None = Field(default=None, max_length=2000)
    field_corrections: list[FieldCorrection] = Field(default_factory=list, max_length=50)

    @field_validator("reviewer")
    @classmethod
    def _reviewer(cls, v: str) -> str:
        v = v.strip()
        if not _REVIEWER.fullmatch(v):
            raise ValueError("reviewer must be a non-empty name (letters, digits, space and . _ @ ' - only)")
        return v

    @field_validator("comments")
    @classmethod
    def _comments(cls, v: str | None) -> str | None:
        v = (v or "").strip()
        return v or None


class ReviewResponse(BaseModel):
    invoice_id: str
    human_check_count: int
    status: Literal["APPROVED", "NEEDS_REPORT_REVIEW", "REJECTED", "SKIPPED_MAX_RETRIES"]
    validation_status: Literal["passed", "failed", "partial", "skipped_max_retries"]
    recommendation: Literal["approve", "review", "reject"]
    discrepancies_count: int
    validation_run: int | None = None
    warnings: list[str] = Field(default_factory=list)


class ReviewState(BaseModel):
    """Everything the review dialog needs. Live (checkpoint) while the invoice is pending, PostgreSQL once finalized."""

    invoice_id: str
    status: str                       # PROCESSING | NEEDS_REPORT_REVIEW | APPROVED | REJECTED | SKIPPED_MAX_RETRIES | LEGACY_FINALIZED
    awaiting_review: bool
    human_check_count: int
    max_human_checks: int
    remaining_checks: int
    validation_run: int | None = None
    validation_status: str | None = None
    recommendation: str | None = None
    invoice: dict[str, Any] | None = None             # editable fields (header + line items), current values
    corrections: dict[str, str] = Field(default_factory=dict)
    report: dict[str, Any] | None = None              # latest validation run
    ai_audit_report: dict[str, Any] | None = None     # immutable run-1 report
    warnings: list[str] = Field(default_factory=list)


# ---- RAG -----------------------------------------------------------------------------------------------
class RagQuery(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    invoice_id: str | None = None
    vendor: str | None = None
    session_id: str | None = None


class RagSource(BaseModel):
    source_type: str
    chunk_text: str
    invoice_id: str | None = None
    invoice_number: str | None = None
    ref: str | None = None
    score: float | None = None
    model_config = {"extra": "allow"}


class RagasMetrics(BaseModel):
    """RAGAS scores. Every value is null unless `state` is done (failed: `error` says why)."""

    context_relevance: float | None = None
    faithfulness: float | None = None
    answer_relevance: float | None = None
    low_confidence_warning: bool | None = None
    state: Literal["pending", "done", "failed", "not_applicable", "disabled"] = "pending"
    error: str | None = None


class RagAnswer(BaseModel):
    invoice_id: str | None = None
    answer: str
    cache_hit: bool = False
    ragas_metrics: RagasMetrics = Field(default_factory=RagasMetrics)
    sources: list[RagSource] = Field(default_factory=list)
    route: str = "open"
    eval_id: str | None = Field(default=None, description="GET /rag/evaluations/{eval_id} returns the stored RAGAS scores")
    retry_count: int = Field(default=0, description="extra retrieval rounds run because a RAGAS score was below its threshold")


class RagEvaluation(RagasMetrics):
    eval_id: str | None = None
