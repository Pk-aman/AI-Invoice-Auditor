"""PostgreSQL connection pools (psycopg3). Role: agent_rw (no access to the erp schema)."""
from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8, search_path: str | None = None) -> ConnectionPool:
    kwargs: dict = {"row_factory": dict_row}
    if search_path:
        # used for the LangGraph checkpointer so its tables land in the `langgraph` schema
        kwargs.update({"autocommit": True, "prepare_threshold": 0, "options": f"-c search_path={search_path}"})
    pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size, kwargs=kwargs, open=False)
    pool.open(wait=True, timeout=10)
    return pool
