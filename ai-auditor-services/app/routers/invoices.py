import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import Connection

from app.core.config import get_settings
from app.core.db import get_conn
from app.core.errors import AppError
from app.repositories import feedback_repo, invoice_repo
from app.schemas.audit import (
    Discrepancy,
    Feedback,
    FeedbackIn,
    InvoiceDetail,
    InvoiceList,
    InvoiceText,
    LineItem,
    ProcessingEvent,
)
from app.services import feedback_service, report_service

router = APIRouter(prefix="/api/v1/invoices", tags=["invoices"])


def _require(conn: Connection, invoice_id: uuid.UUID) -> None:
    if not invoice_repo.invoice_exists(conn, invoice_id):
        raise AppError(404, "invoice_not_found", "Invoice not found.")


@router.get("", response_model=InvoiceList)
def list_invoices(
    status: str | None = None,
    recommendation: Literal["approve", "review", "reject"] | None = None,
    vendor: str | None = None,
    currency: str | None = Query(None, min_length=3, max_length=3),
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    sort: str = "processed_at",
    order: Literal["asc", "desc"] = "desc",
    conn: Connection = Depends(get_conn),
):
    items, total = invoice_repo.list_invoices(
        conn, status=status, recommendation=recommendation, vendor=vendor, currency=currency,
        date_from=date_from, date_to=date_to, q=q, page=page, page_size=page_size, sort=sort, order=order,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: uuid.UUID, conn: Connection = Depends(get_conn)):
    inv = invoice_repo.get_invoice(conn, invoice_id)
    if not inv:
        raise AppError(404, "invoice_not_found", "Invoice not found.")
    inv["pending_corrections"] = feedback_repo.pending_count(conn, invoice_id)
    inv["corrections"] = feedback_repo.latest_corrections(conn, invoice_id)
    return inv


@router.get("/{invoice_id}/line-items", response_model=list[LineItem])
def get_line_items(invoice_id: uuid.UUID, conn: Connection = Depends(get_conn)):
    _require(conn, invoice_id)
    return invoice_repo.line_items(conn, invoice_id)


@router.get("/{invoice_id}/discrepancies", response_model=list[Discrepancy])
def get_discrepancies(invoice_id: uuid.UUID, run: Literal["latest", "all"] = "latest",
                      conn: Connection = Depends(get_conn)):
    _require(conn, invoice_id)
    return invoice_repo.discrepancies(conn, invoice_id, run)


@router.get("/{invoice_id}/text", response_model=InvoiceText)
def get_text(invoice_id: uuid.UUID, kind: Literal["raw", "translated"] = "translated",
             conn: Connection = Depends(get_conn)):
    found, text_ = invoice_repo.get_text(conn, invoice_id, kind)
    if not found:
        raise AppError(404, "invoice_not_found", "Invoice not found.")
    return {"invoice_id": invoice_id, "kind": kind, "text": text_}


@router.get("/{invoice_id}/events", response_model=list[ProcessingEvent])
def get_events(invoice_id: uuid.UUID, conn: Connection = Depends(get_conn)):
    _require(conn, invoice_id)
    return invoice_repo.events_for_invoice(conn, invoice_id)


@router.get("/{invoice_id}/report")
def get_report(invoice_id: uuid.UUID, format: Literal["html", "json"] = "html",
               conn: Connection = Depends(get_conn)):
    _require(conn, invoice_id)
    path = report_service.resolve_report_path(get_settings().reports_dir, invoice_id, format)
    if path is None:
        raise AppError(404, "report_not_found", "No report has been generated for this invoice yet.")
    media = "text/html; charset=utf-8" if format == "html" else "application/json"
    return FileResponse(path, media_type=media, headers={
        "Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})


@router.post("/{invoice_id}/feedback", response_model=Feedback, status_code=201)
def post_feedback(invoice_id: uuid.UUID, body: FeedbackIn, conn: Connection = Depends(get_conn)):
    return feedback_service.add_feedback(conn, invoice_id, body.field_name, body.corrected_value, body.corrected_by)


@router.get("/{invoice_id}/feedback", response_model=list[Feedback])
def get_feedback(invoice_id: uuid.UUID, conn: Connection = Depends(get_conn)):
    _require(conn, invoice_id)
    return feedback_repo.history(conn, invoice_id)
