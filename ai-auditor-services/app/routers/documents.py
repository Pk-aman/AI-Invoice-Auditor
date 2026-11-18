import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import Connection

from app.core.config import get_settings
from app.core.db import get_conn
from app.core.errors import AppError
from app.repositories import document_repo, invoice_repo
from app.schemas.audit import DocumentDetail, DocumentList, DocumentStats, DocumentSummary, Finding
from app.services.report_service import resolve_report_path

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

_PS = "^(audited|processing|failed|awaiting)$"
_AR = "^(no_issues|needs_attention|not_available)$"


def _valid_id(document_id: str) -> str:
    if len(document_id) != 16 or any(c not in "0123456789abcdef" for c in document_id):
        raise AppError(422, "invalid_document_id", "Invalid document id.")
    return document_id


def _summary(row: dict) -> dict:
    ext = Path(row["filename"] or "").suffix.lower().lstrip(".")
    return {**row, "file_type": ext}


@router.get("", response_model=DocumentList)
def list_documents(
    q: str | None = Query(None, max_length=100),
    processing_status: str | None = Query(None, pattern=_PS),
    audit_result: str | None = Query(None, pattern=_AR),
    uploaded_within_days: int | None = Query(None, ge=1, le=3650),
    page: int = Query(1, ge=1),
    page_size: int = Query(8, ge=1, le=100),
    conn: Connection = Depends(get_conn),
):
    rows, total = document_repo.list_documents(
        conn, q=q, processing_status=processing_status, audit_result=audit_result,
        within_days=uploaded_within_days, page=page, page_size=page_size)
    return {"items": [_summary(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/stats", response_model=DocumentStats)
def document_stats(conn: Connection = Depends(get_conn)):
    return document_repo.stats(conn)


def _findings(conn: Connection, row: dict) -> tuple[list[dict], str | None, list[str]]:
    """Findings come from the stored report JSON (full evidence); fall back to the discrepancy rows."""
    findings: list[dict] = []
    summary, reasons = None, []
    inv = row.get("invoice_id")
    if inv is None:
        return findings, summary, reasons
    path = resolve_report_path(get_settings().reports_dir, inv, "json")
    if path:
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rep = None
        if rep:
            summary = rep.get("summary")
            reasons = list(rep.get("reasons") or [])
            for f in rep.get("data_findings") or []:
                findings.append({"severity": f.get("severity") or "medium", "message": f.get("message") or f.get("code", "")})
            for d in rep.get("discrepancies") or []:
                if d.get("code") == "po_not_found":
                    msg = "Purchase order was not found in the ERP."
                elif d.get("code") == "vendor_not_found":
                    msg = "Vendor was not found in the ERP."
                else:
                    msg = f"{d.get('field_name')}: invoice has {d.get('invoice_value')}, ERP has {d.get('erp_value')}"
                    if d.get("deviation_pct") not in (None, ""):
                        msg += f" ({float(d['deviation_pct']):.1f}% difference)"
                findings.append({"severity": d.get("severity") or "medium", "message": msg})
            return findings, summary, reasons
    for d in invoice_repo.discrepancies(conn, inv, "latest"):
        findings.append({"severity": d.get("severity") or "medium",
                         "message": f"{d['field_name']}: invoice has {d['invoice_value']}, ERP has {d['erp_value']}"})
    return findings, summary, reasons


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, conn: Connection = Depends(get_conn)):
    row = document_repo.get_document(conn, _valid_id(document_id))
    if row is None:
        raise AppError(404, "document_not_found", "This invoice could not be found.")
    findings, summary, reasons = _findings(conn, row)
    return {**_summary(row), "findings": [Finding(**f) for f in findings], "summary": summary, "reasons": reasons}


@router.get("/{document_id}/file")
def original_file(document_id: str, conn: Connection = Depends(get_conn)):
    row = document_repo.get_document(conn, _valid_id(document_id))
    if row is None or not row.get("path"):
        raise AppError(404, "file_not_found", "The original document is not available.")
    base = get_settings().incoming_dir.resolve()
    candidate = (base / Path(row["path"]).name).resolve()
    if base not in candidate.parents or not candidate.is_file():
        raise AppError(404, "file_not_found", "The original document is not available.")
    media = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    return FileResponse(candidate, media_type=media, filename=row["filename"] or candidate.name,
                        content_disposition_type="inline")
