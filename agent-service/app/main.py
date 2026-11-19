"""FastAPI app factory. Startup order: config -> dependency probe (fail fast) -> wiring -> monitor -> serve."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import time
import uuid

from fastapi import FastAPI, Request

from app.api.routes import router
from app.core import startup
from app.core.config import Settings, get_settings
from app.core.context import AppContext
from app.core import observability
from app.core.logging import configure_logging, request_id
from app.services.review import ReviewService
from app.services.pipeline import Pipeline
from app.services.rag_service import RagService

log = logging.getLogger(__name__)


def create_app(*, ctx: AppContext | None = None, start_monitor: bool = True, probe=None) -> FastAPI:
    """Pass `ctx` (and probe) to inject test doubles; otherwise everything is wired from the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings: Settings = ctx.settings if ctx else get_settings()
        configure_logging(settings.log_level)
        observability.configure(settings)  # optional LangFuse; never raises
        the_probe = probe or startup.probe_all
        the_ctx = ctx
        if the_ctx is None:
            log.info("starting agent-service", extra={"env": settings.app_env})
            startup.wait_for_dependencies(settings, probe=the_probe)  # raises -> process exits non-zero
            the_ctx = startup.build_context(settings)
        pipeline = Pipeline(the_ctx)
        app.state.ctx, app.state.pipeline, app.state.rag, app.state.probe = the_ctx, pipeline, RagService(the_ctx), the_probe
        app.state.review = ReviewService(the_ctx, pipeline)
        if start_monitor:
            pipeline.start_monitor()
        log.info("agent-service ready")
        try:
            yield
        finally:
            pipeline.shutdown()
            observability.get_tracer().flush()
            if ctx is None:
                for closer in (the_ctx.pool, the_ctx.extras.get("checkpoint_pool")):
                    if closer is not None:
                        closer.close()

    app = FastAPI(title="agent-service", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_context(req: Request, call_next):
        rid = req.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(req)
        finally:
            request_id.reset(token)
        response.headers["x-request-id"] = rid
        if req.url.path not in ("/health", "/ready"):
            log.info("request", extra={"request_id": rid, "method": req.method, "path": req.url.path,
                                       "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        return response

    app.include_router(router)
    return app


app = create_app()
