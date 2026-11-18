"""API request/response DTOs."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProcessRequest(BaseModel):
    file_path: str = Field(description="Path of a file under the incoming directory")


class ProcessResponse(BaseModel):
    status: str  # queued | duplicate
    invoice_id: str | None = None


class RevalidateResponse(BaseModel):
    invoice_id: str
    validation_run: int
    status: str = "queued"


class StatusResponse(BaseModel):
    invoice_id: str
    stage: str | None
    state: str | None
    error: str | None = None
    events: list[dict[str, Any]] = []


class RagQuery(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    invoice_id: str | None = None
    vendor: str | None = None
    session_id: str | None = None


class RagAnswer(BaseModel):
    answer: str
    route: str
    sources: list[dict[str, Any]]
    eval_id: str | None = None


class RagEvaluation(BaseModel):
    state: str
    error: str | None = None
    relevance: float | None = None
    groundedness: float | None = None
    context_relevance: float | None = None
