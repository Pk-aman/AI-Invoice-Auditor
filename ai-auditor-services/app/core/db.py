import logging
import sys
import time
from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings

log = logging.getLogger(__name__)


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def get_conn() -> Iterator[Connection]:
    """One transaction per request; commits on success, rolls back on error."""
    with get_engine().begin() as conn:
        yield conn


def db_ok() -> tuple[bool, str | None]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except SQLAlchemyError as exc:
        return False, exc.__class__.__name__


def wait_for_db_or_exit() -> None:
    """Fail fast: bounded retry, then exit non-zero (never run half-broken)."""
    deadline = time.monotonic() + get_settings().db_wait_seconds
    attempt = 0
    while True:
        attempt += 1
        ok, err = db_ok()
        if ok:
            log.info("database reachable after %d attempt(s)", attempt)
            return
        if time.monotonic() >= deadline:
            log.error("database unreachable (%s); giving up", err)
            sys.exit(1)
        log.warning("database not ready (%s); retrying", err)
        time.sleep(2)
