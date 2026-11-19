"""Upload handling: validate -> stream+hash -> duplicate check -> atomic rename into incoming/.

The agent's monitor must ignore the hidden `.tmp/` directory; files only ever appear in
`incoming/` via os.replace(), so a half-written file is never visible there.
"""

import hashlib
import logging
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import Connection

from app.repositories import invoice_repo

log = logging.getLogger(__name__)

ALLOWED_EXT = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
CHUNK = 1024 * 1024
MAX_NAME = 120


def sanitise_filename(name: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "invoice"
    ext = re.sub(r"[^A-Za-z0-9.]", "", ext).lower()
    return stem[: MAX_NAME - len(ext)] + ext


def sniff_ok(ext: str, path: Path) -> bool:
    """Content must match the extension family (magic bytes), not just the name."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext in (".tif", ".tiff"):
        return head[:4] in (b"II*\x00", b"MM\x00*")
    if ext == ".docx":
        if not head.startswith(b"PK\x03\x04"):
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                return "word/document.xml" in zf.namelist()
        except zipfile.BadZipFile:
            return False
    return False


class TooLarge(Exception):
    pass


def stream_to_temp(src: BinaryIO, tmp_dir: Path, max_bytes: int) -> tuple[Path, str, int]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.part"
    sha, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as out:
            while chunk := src.read(CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge
                sha.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, sha.hexdigest(), size


def already_queued(incoming: Path, checksum: str) -> str | None:
    prefix = f"{checksum[:12]}_"
    for p in incoming.iterdir():
        if p.is_file() and p.name.startswith(prefix):
            return p.name
    return None


def process_upload(
    conn: Connection, *, filename: str, fileobj: BinaryIO, incoming: Path, max_bytes: int
) -> dict:
    safe = sanitise_filename(filename)
    ext = os.path.splitext(safe)[1]
    if ext not in ALLOWED_EXT:
        return {"filename": filename, "status": "REJECTED", "reason": f"Unsupported file type '{ext or 'none'}'. Allowed: PDF, DOCX, PNG, JPG, TIFF."}

    incoming.mkdir(parents=True, exist_ok=True)
    try:
        tmp, checksum, size = stream_to_temp(fileobj, incoming / ".tmp", max_bytes)
    except TooLarge:
        return {"filename": filename, "status": "REJECTED", "reason": f"File is larger than {max_bytes // (1024 * 1024)} MB."}
    if size == 0:
        tmp.unlink(missing_ok=True)
        return {"filename": filename, "status": "REJECTED", "reason": "File is empty."}
    if not sniff_ok(ext, tmp):
        tmp.unlink(missing_ok=True)
        return {"filename": filename, "status": "REJECTED", "reason": f"File content does not look like a valid {ext[1:].upper()} file."}

    existing = invoice_repo.find_invoice_by_checksum(conn, checksum)
    queued_name = None if existing else already_queued(incoming, checksum)
    if existing or queued_name:
        tmp.unlink(missing_ok=True)
        invoice_repo.insert_event(
            conn, file_path=None, checksum=checksum, stage="QUEUED", state="DUPLICATE",
            detail={"original_filename": filename, "already_audited": bool(existing), "stored_name": queued_name},
        )
        return {
            "filename": filename, "status": "DUPLICATE", "checksum": checksum, "invoice_id": existing,
            "reason": "This invoice has already been processed." if existing else "This invoice is already queued for processing.",
        }

    final = incoming / f"{checksum[:12]}_{safe}"
    os.replace(tmp, final)  # atomic within one filesystem
    invoice_repo.insert_event(
        conn, file_path=str(final), checksum=checksum, stage="QUEUED", state="OK",
        detail={"original_filename": filename, "stored_name": final.name, "size_bytes": size},
    )
    log.info("upload queued", extra={"checksum": checksum})
    return {"filename": filename, "status": "QUEUED", "checksum": checksum, "upload_id": checksum[:12]}


# --- status derivation ---------------------------------------------------------------------------
STAGE_ORDER = ["QUEUED", "DETECTED", "EXTRACT", "TRANSLATE", "DEDUPE", "INGEST", "PARSE", "DATA_VALIDATION",
               "BUSINESS_VALIDATION", "REPORT", "INDEX", "HUMAN_REVIEW", "REVALIDATE", "FINALIZE", "COMPLETED"]


def derive_status(events: list[dict], invoice_id) -> dict | None:
    """Map the processing-event log of one file to an upload status."""
    content_dup = next((e for e in reversed(events) if e["stage"] == "DEDUPE" and e["state"] == "DUPLICATE"), None)
    real = [e for e in events if e["state"] != "DUPLICATE"]
    if content_dup is not None:  # same invoice content already audited under another file
        existing = (content_dup.get("detail") or {}).get("existing_invoice_id")
        return {"status": "DUPLICATE", "stage": "DEDUPE", "state": "DUPLICATE", "existing_invoice_id": existing,
                "updated_at": content_dup["created_at"], "error": None}
    if not real and invoice_id is None:
        return {"status_none": True} if not events else {"status": "QUEUED", "stage": "QUEUED", "state": "DUPLICATE"}
    last = real[-1] if real else None
    out = {"stage": last["stage"] if last else None, "state": last["state"] if last else None,
           "updated_at": last["created_at"] if last else None, "error": None}
    if any(e["stage"] == "COMPLETED" and e["state"] == "OK" for e in real):
        out["status"] = "COMPLETED"
    elif last and last["state"] in ("FAILED", "REJECTED"):
        out["status"] = "FAILED"
        detail = last.get("detail") or {}
        out["error"] = detail.get("error") or detail.get("reason") or "Processing failed."
    elif last and last["stage"] == "HUMAN_REVIEW" and last["state"] == "PENDING":
        out["status"] = "NEEDS_REVIEW"
    elif last and last["stage"] == "QUEUED":
        out["status"] = "QUEUED"
    else:
        out["status"] = "PROCESSING"
    return out
