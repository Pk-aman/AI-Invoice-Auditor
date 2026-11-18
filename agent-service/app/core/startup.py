"""Dependency probing (fail-fast) and application wiring."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx
import redis as redis_lib

from app.clients.erp_client import ErpClient
from app.clients.qdrant_client import ensure_collection, make_client
from app.core.config import Settings
from app.core.context import AppContext
from app.core.db import make_pool
from app.core.llm import CachedLLM, LiteLLMClient, llm_config_problem
from app.core.rules import load_rules
from app.persistence.audit_repo import AuditRepo

log = logging.getLogger(__name__)


class DependencyError(RuntimeError):
    pass


def check_postgres(s: Settings) -> str | None:
    import psycopg

    try:
        with psycopg.connect(s.database_url, connect_timeout=3) as conn:
            conn.execute("SELECT 1 FROM audit.invoice_audit LIMIT 1")
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def check_redis(s: Settings) -> str | None:
    try:
        redis_lib.Redis.from_url(s.redis_url, socket_connect_timeout=3).ping()
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def check_qdrant(s: Settings) -> str | None:
    try:
        r = httpx.get(s.qdrant_url.rstrip("/") + "/readyz", timeout=3)
        return None if r.status_code == 200 else f"readyz returned {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def check_erp(s: Settings) -> str | None:
    try:
        r = httpx.get(s.erp_base_url.rstrip("/") + "/health", timeout=3)
        return None if r.status_code == 200 else f"/health returned {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def check_llm(s: Settings) -> str | None:
    if s.llm_probe.lower() == "off":
        return None
    problem = llm_config_problem(s)
    if problem:
        return problem
    models = {s.llm_reasoning_model, s.llm_translation_model, s.llm_generation_model, s.embedding_model}
    if any(m.startswith("ollama") for m in models):
        try:
            r = httpx.get(s.ollama_base_url.rstrip("/") + "/api/tags", timeout=3)
            return None if r.status_code == 200 else f"ollama /api/tags returned {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            return f"Ollama unreachable at {s.ollama_base_url}: {type(exc).__name__}"
    return None


def probe_all(s: Settings) -> dict[str, str | None]:
    """name -> None when healthy, else the problem."""
    return {"postgres": check_postgres(s), "redis": check_redis(s), "qdrant": check_qdrant(s), "erp": check_erp(s), "llm": check_llm(s)}


def wait_for_dependencies(s: Settings, timeout_s: float = 60, interval_s: float = 2,
                          probe: Callable[[Settings], dict[str, str | None]] = probe_all) -> None:
    """Bounded retry; raises DependencyError (process exits non-zero) instead of starting half-broken."""
    deadline = time.monotonic() + timeout_s
    while True:
        status = probe(s)
        bad = {k: v for k, v in status.items() if v}
        if not bad:
            return
        if time.monotonic() >= deadline:
            raise DependencyError("dependencies unavailable: " + "; ".join(f"{k}: {v}" for k, v in bad.items()))
        log.warning("waiting for dependencies", extra={"unavailable": list(bad)})
        time.sleep(interval_s)


def build_context(s: Settings) -> AppContext:
    from langgraph.checkpoint.postgres import PostgresSaver

    rules = load_rules(s.rules_path)
    pool = make_pool(s.database_url)
    cp_pool = make_pool(s.database_url, max_size=4, search_path="langgraph")
    checkpointer = PostgresSaver(cp_pool)
    checkpointer.setup()  # creates the checkpoint tables inside the `langgraph` schema

    redis_client = redis_lib.Redis.from_url(s.redis_url)
    llm = CachedLLM(LiteLLMClient(s), redis_client, s.llm_cache_ttl_s)
    qdrant = make_client(s.qdrant_url)
    ensure_collection(qdrant, s.qdrant_collection, s.embedding_dim)  # raises VectorDimMismatch on a size clash
    ctx = AppContext(settings=s, rules=rules, repo=AuditRepo(pool), llm=llm, erp=ErpClient(s.erp_base_url), qdrant=qdrant,
                     redis=redis_client, pool=pool, checkpointer=checkpointer)
    ctx.extras["checkpoint_pool"] = cp_pool
    return ctx
