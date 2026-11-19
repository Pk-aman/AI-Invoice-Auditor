"""Value objects for the two-write persistence lifecycle and the human-in-the-loop review."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.models import Discrepancy, ExtractedInvoice

FinalStatus = Literal["APPROVED", "REJECTED", "SKIPPED_MAX_RETRIES"]
Decision = Literal["approve", "reject_and_revalidate", "reject"]


class Ingestion(BaseModel):
    """SQL Write #1 payload: ingestion evidence only (no parsed fields, no validation output)."""

    invoice_id: str
    file_path: str
    file_checksum: str
    content_checksum: str
    extraction_method: str
    raw_extracted_text: str
    translated_text: str
    source_language: str
    translation_confidence: float
    ocr_confidence: float | None = None


class FeedbackItem(BaseModel):
    field_name: str
    original_value: str | None = None
    corrected_value: str | None = None
    corrected_by: str
    review_round: int = Field(ge=1)
    revalidated: bool = False


class DecisionItem(BaseModel):
    review_round: int = Field(ge=1)
    decision: Decision
    reviewer: str
    comments: str | None = None
    human_check_count: int


class Finalization(BaseModel):
    """SQL Write #2 payload."""

    status: FinalStatus
    validation_status: str
    recommendation: str
    validation_run: int
    human_check_count: int
    invoice: ExtractedInvoice
    vendor_id: str | None = None
    report_path: str | None = None
    ai_audit_report: dict[str, Any]                      # run-1 report: immutable audit snapshot
    run_reports: dict[int, dict[str, Any]]               # every validation run's report
    discrepancy_runs: dict[int, list[Discrepancy]]       # every validation run's discrepancies
    feedback: list[FeedbackItem] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
