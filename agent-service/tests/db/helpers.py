"""Test helpers: run an invoice to the review pause and build review requests."""
from __future__ import annotations

from pathlib import Path

from app.api.schemas import FieldCorrection, ReviewRequest
from app.services.pipeline import Pipeline
from app.services.review import ReviewService
from tests.conftest import GOOD_INVOICE, FakeLLM, make_docx


def price_drift_invoice() -> dict:
    inv = {**GOOD_INVOICE, "line_items": [dict(li) for li in GOOD_INVOICE["line_items"]]}
    inv["line_items"][0]["unit_price"] = 14.00  # ERP says 12.00
    inv["line_items"][0]["line_total"] = 700
    inv["subtotal"] = inv["total_amount"] = 1060
    return inv


def start(make_ctx, tmp_path: Path, parsed: dict | None = None, name: str = "inv.docx", text: str | None = None, **ctx_kwargs):
    """-> (ctx, pipeline, review_service, invoice_id) with the invoice parked at the human-review interrupt."""
    ctx = make_ctx(llm=FakeLLM(parsed or GOOD_INVOICE), **ctx_kwargs)
    pipe = Pipeline(ctx)
    path = make_docx(tmp_path / name) if text is None else make_docx(tmp_path / name, text)
    out = pipe.process_path(path)
    assert out["status"] == "NEEDS_REPORT_REVIEW", out
    return ctx, pipe, ReviewService(ctx, pipe), out["invoice_id"]


def req(decision: str, reviewer: str = "auditor_jane", comments: str | None = None, **corrections: str) -> ReviewRequest:
    return ReviewRequest(decision=decision, reviewer=reviewer, comments=comments,
                         field_corrections=[FieldCorrection(field_name=k.replace("__", ":"), corrected_value=v) for k, v in corrections.items()])
