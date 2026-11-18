from fastapi import APIRouter, Response

from app.core.config import get_settings
from app.core.db import db_ok

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict:
    """Readiness: dependencies reachable (503 otherwise)."""
    s = get_settings()
    ok, err = db_ok()
    dirs_ok = True
    try:
        s.incoming_dir.mkdir(parents=True, exist_ok=True)
        dirs_ok = s.incoming_dir.is_dir() and _writable(s.incoming_dir)
    except OSError:
        dirs_ok = False
    healthy = ok and dirs_ok
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "unavailable",
        "database": "ok" if ok else f"down ({err})",
        "incoming_dir": "ok" if dirs_ok else "not writable",
    }


def _writable(path) -> bool:
    import os

    return os.access(path, os.W_OK)
