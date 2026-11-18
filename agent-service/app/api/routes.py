"""HTTP API (plan section 8)."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.schemas import ProcessRequest, ProcessResponse, RagAnswer, RagEvaluation, RagQuery, RevalidateResponse, StatusResponse

log = logging.getLogger(__name__)
router = APIRouter()

_READY_CACHE: dict = {"at": 0.0, "status": None}
_READY_TTL_S = 5.0


def _state(request: Request):
    return request.app.state


def _valid_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid invoice id") from None


@router.get("/health", tags=["health"])
def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/ready", tags=["health"])
def ready(request: Request):
    """Readiness: every dependency (PostgreSQL, Redis, Qdrant, ERP, LLM config) is usable."""
    st = _state(request)
    now = time.monotonic()
    if now - _READY_CACHE["at"] > _READY_TTL_S or _READY_CACHE["status"] is None:
        _READY_CACHE.update(at=now, status=st.probe(st.ctx.settings))
    status = _READY_CACHE["status"]
    ok = not any(status.values())
    body = {"ready": ok, "dependencies": {k: ("ok" if v is None else v) for k, v in status.items()}}
    return JSONResponse(body, status_code=200 if ok else 503)


@router.post("/invoices/process", response_model=ProcessResponse, status_code=202, tags=["invoices"])
def process(req: ProcessRequest, request: Request):
    st = _state(request)
    incoming = Path(st.ctx.settings.incoming_dir).resolve()
    path = Path(req.file_path).resolve()
    if incoming != path and incoming not in path.parents:
        raise HTTPException(status_code=400, detail="file_path must be inside the incoming directory")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    verdict, existing = st.pipeline.precheck(path)
    if verdict == "unsupported":
        raise HTTPException(status_code=415, detail=f"unsupported file type {path.suffix}")
    if verdict == "duplicate":
        return JSONResponse({"status": "duplicate", "invoice_id": existing}, status_code=200)
    iid = st.pipeline.submit_path(path)
    return ProcessResponse(status="queued", invoice_id=iid)


@router.get("/invoices/{invoice_id}/status", response_model=StatusResponse, tags=["invoices"])
def status(invoice_id: str, request: Request):
    iid = _valid_uuid(invoice_id)
    events = _state(request).ctx.repo.events(iid)
    if not events:
        raise HTTPException(status_code=404, detail="no events for this invoice")
    last = events[-1]
    err = next((e["detail"].get("error") for e in reversed(events) if e["state"] == "FAILED" and e["detail"]), None)
    return StatusResponse(invoice_id=iid, stage=last["stage"], state=last["state"], error=err,
                          events=[{"stage": e["stage"], "state": e["state"], "at": e["created_at"].isoformat()} for e in events])


@router.post("/invoices/{invoice_id}/revalidate", response_model=RevalidateResponse, status_code=202, tags=["invoices"])
def revalidate(invoice_id: str, request: Request):
    iid = _valid_uuid(invoice_id)
    st = _state(request)
    if st.ctx.repo.get_invoice(iid) is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    try:
        run = st.pipeline.submit_revalidate(iid)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RevalidateResponse(invoice_id=iid, validation_run=run)


@router.post("/rag/query", response_model=RagAnswer, tags=["rag"])
def rag_query(body: RagQuery, request: Request):
    st = _state(request)
    if body.invoice_id:
        body.invoice_id = _valid_uuid(body.invoice_id)
    try:
        return RagAnswer(**st.rag.query(body.question, body.invoice_id, body.vendor))
    except Exception as exc:  # noqa: BLE001
        log.exception("rag query failed")
        raise HTTPException(status_code=502, detail=f"question could not be answered: {type(exc).__name__}") from exc


@router.get("/rag/evaluations/{eval_id}", response_model=RagEvaluation, tags=["rag"])
def rag_evaluation(eval_id: str, request: Request):
    res = _state(request).rag.evaluation(_valid_uuid(eval_id))
    if res is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return RagEvaluation(**res)


@router.post("/rag/reindex", status_code=202, tags=["rag"])
def rag_reindex(request: Request, invoice_id: str | None = None):
    st = _state(request)
    iid = _valid_uuid(invoice_id) if invoice_id else None
    try:
        points = st.rag.reindex(iid)
    except Exception as exc:  # noqa: BLE001
        log.exception("reindex failed")
        raise HTTPException(status_code=502, detail=f"reindex failed: {type(exc).__name__}: {exc}") from exc
    return {"status": "done", "points": points}

