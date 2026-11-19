import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import db_ok

router = APIRouter(tags=["health"])


def _writable(path) -> bool:
    return os.access(path, os.W_OK)


def _check() -> JSONResponse:
    """{"status": "healthy", "dependencies": {"postgres": "connected", "incoming_dir": "writable"}}; 503 when a
    dependency this service needs to do its job (the database, the shared incoming volume) is unavailable."""
    s = get_settings()
    ok, err = db_ok()
    try:
        s.incoming_dir.mkdir(parents=True, exist_ok=True)
        dir_ok = s.incoming_dir.is_dir() and _writable(s.incoming_dir)
    except OSError:
        dir_ok = False
    healthy = ok and dir_ok
    body = {
        "status": "healthy" if healthy else "unhealthy",
        "dependencies": {
            "postgres": "connected" if ok else f"unreachable: {err}",
            "incoming_dir": "writable" if dir_ok else "not writable",
        },
    }
    return JSONResponse(body, status_code=200 if healthy else 503)


@router.get("/health")
def health() -> JSONResponse:
    return _check()


@router.get("/ready")
def ready() -> JSONResponse:
    """Same contract as /health (kept for the Docker healthcheck and older callers)."""
    return _check()
