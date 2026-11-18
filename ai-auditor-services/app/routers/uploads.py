from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy import Connection

from app.core.config import get_settings
from app.core.db import get_conn
from app.core.errors import AppError
from app.repositories import invoice_repo
from app.schemas.audit import UploadResult, UploadStatus
from app.services import upload_service

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])


@router.post("", response_model=list[UploadResult], status_code=202)
def upload(response: Response, files: list[UploadFile] = File(...), conn: Connection = Depends(get_conn)):
    s = get_settings()
    if len(files) > 50:
        raise AppError(422, "too_many_files", "Upload at most 50 files at a time.")
    results = [
        upload_service.process_upload(
            conn, filename=f.filename or "invoice", fileobj=f.file, incoming=s.incoming_dir, max_bytes=s.max_upload_bytes
        )
        for f in files
    ]
    if not any(r["status"] == "QUEUED" for r in results):
        response.status_code = 200
    return results


@router.get("/{checksum}/status", response_model=UploadStatus)
def upload_status(checksum: str, conn: Connection = Depends(get_conn)):
    if len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
        raise AppError(422, "invalid_checksum", "Checksum must be a 64-character hex sha256.")
    invoice_id = invoice_repo.find_invoice_by_checksum(conn, checksum)
    events = invoice_repo.events_by_checksum(conn, checksum)
    if invoice_id is None and not events:
        raise AppError(404, "upload_not_found", "No upload with this checksum.")
    derived = upload_service.derive_status(events, invoice_id)
    if derived.get("status_none"):
        raise AppError(404, "upload_not_found", "No upload with this checksum.")
    if invoice_id is not None and "status" not in derived:
        derived["status"] = "PROCESSING"
    return {"checksum": checksum, "invoice_id": invoice_id, **derived}
