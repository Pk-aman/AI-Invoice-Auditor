import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import wait_for_db_or_exit
from app.core.errors import install_error_handlers
from app.core.logging import setup_logging
from app.routers import erp, health, invoices, stats, uploads

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    wait_for_db_or_exit()  # fail fast: exits non-zero if the database never becomes reachable
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    log.info("ai-auditor-services started (env=%s)", settings.app_env)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Auditor Services", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST"], allow_headers=["*"]
    )
    install_error_handlers(app)

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        if request.url.path not in ("/health", "/ready"):
            log.info("request", extra={
                "request_id": rid, "method": request.method, "path": request.url.path,
                "status": response.status_code, "duration_ms": round((time.perf_counter() - start) * 1000, 1)})
        return response

    for r in (health.router, erp.router, uploads.router, invoices.router, stats.router):
        app.include_router(r)
    return app


app = create_app()
