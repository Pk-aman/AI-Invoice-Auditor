"""FastAPI app factory. Startup order: config -> dependency probe (fail fast) -> wiring -> monitor -> serve."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core import startup
from app.core.config import Settings, get_settings
from app.core.context import AppContext
from app.core.logging import configure_logging
from app.services.pipeline import Pipeline
from app.services.rag_service import RagService

log = logging.getLogger(__name__)


def create_app(*, ctx: AppContext | None = None, start_monitor: bool = True, probe=None) -> FastAPI:
    """Pass `ctx` (and probe) to inject test doubles; otherwise everything is wired from the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings: Settings = ctx.settings if ctx else get_settings()
        configure_logging(settings.log_level)
        the_probe = probe or startup.probe_all
        the_ctx = ctx
        if the_ctx is None:
            log.info("starting agent-service", extra={"env": settings.app_env})
            startup.wait_for_dependencies(settings, probe=the_probe)  # raises -> process exits non-zero
            the_ctx = startup.build_context(settings)
        pipeline = Pipeline(the_ctx)
        app.state.ctx, app.state.pipeline, app.state.rag, app.state.probe = the_ctx, pipeline, RagService(the_ctx), the_probe
        if start_monitor:
            pipeline.start_monitor()
        log.info("agent-service ready")
        try:
            yield
        finally:
            pipeline.shutdown()
            if ctx is None:
                for closer in (the_ctx.pool, the_ctx.extras.get("checkpoint_pool")):
                    if closer is not None:
                        closer.close()

    app = FastAPI(title="agent-service", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
