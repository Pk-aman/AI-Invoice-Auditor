"""Runs the invoice graph: folder monitor thread -> bounded worker pool; re-validation; singleton guard."""
from __future__ import annotations

import logging
import threading
import uuid
import zlib
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.agents.monitor import FolderWatcher, compute_checksum
from app.core.context import AppContext
from app.core.logging import correlation_id
from app.extraction.extract import detect_type
from app.graphs.invoice_graph import build_invoice_graph, build_revalidate_graph

log = logging.getLogger(__name__)

_LOCK_KEY = zlib.crc32(b"ai-auditor-monitor")


class Pipeline:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.invoice_graph = build_invoice_graph(ctx, ctx.checkpointer)
        self.revalidate_graph = build_revalidate_graph(ctx, ctx.checkpointer)
        self.executor = ThreadPoolExecutor(max_workers=max(1, ctx.settings.processing_concurrency), thread_name_prefix="invoice")
        self.watcher: FolderWatcher | None = None
        self._lock_conn = None
        self._inflight: set[str] = set()
        self._guard = threading.Lock()

    # ---- synchronous runs (also used by tests) --------------------------------------
    def process_path(self, path: Path, invoice_id: str | None = None) -> dict:
        iid = invoice_id or str(uuid.uuid4())
        token = correlation_id.set(iid)
        try:
            return self.invoice_graph.invoke({"invoice_id": iid, "file_path": str(path), "validation_run": 1},
                                             {"configurable": {"thread_id": iid}})
        finally:
            correlation_id.reset(token)

    def revalidate_sync(self, invoice_id: str, run: int) -> dict:
        token = correlation_id.set(invoice_id)
        try:
            return self.revalidate_graph.invoke({"invoice_id": invoice_id, "validation_run": run},
                                                {"configurable": {"thread_id": f"{invoice_id}:rv{run}"}})
        finally:
            correlation_id.reset(token)

    # ---- async submission -------------------------------------------------------------
    def submit_path(self, path: Path, invoice_id: str | None = None) -> str:
        iid = invoice_id or str(uuid.uuid4())
        self.executor.submit(self._safe, self.process_path, path, iid)
        return iid

    def submit_revalidate(self, invoice_id: str) -> int:
        with self._guard:
            if invoice_id in self._inflight:
                raise RuntimeError("re-validation already in progress for this invoice")
            self._inflight.add(invoice_id)
        run = self.ctx.repo.next_validation_run(invoice_id)
        fut: Future = self.executor.submit(self._safe, self.revalidate_sync, invoice_id, run)
        fut.add_done_callback(lambda _f: self._release(invoice_id))
        return run

    def _release(self, invoice_id: str) -> None:
        with self._guard:
            self._inflight.discard(invoice_id)

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - background failures must be logged, not lost
            log.exception("background pipeline run crashed")

    def precheck(self, path: Path) -> tuple[str, str | None]:
        """('duplicate', existing_id) | ('queued', None) — used by POST /invoices/process."""
        if detect_type(path) is None:
            return "unsupported", None
        existing = self.ctx.repo.find_by_checksum(compute_checksum(path))
        return ("duplicate", existing) if existing else ("queued", None)

    # ---- monitor lifecycle --------------------------------------------------------------
    def _acquire_singleton(self) -> bool:
        """Session-level advisory lock: only ONE monitor may run (the folder watcher cannot scale horizontally)."""
        if self.ctx.pool is None:
            return True
        conn = self.ctx.pool.getconn()
        conn.autocommit = True
        got = conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (_LOCK_KEY,)).fetchone()["got"]
        if got:
            self._lock_conn = conn
            return True
        self.ctx.pool.putconn(conn)
        return False

    def start_monitor(self) -> bool:
        if not self._acquire_singleton():
            log.warning("another agent instance holds the monitor lock; folder monitoring disabled here")
            return False
        s = self.ctx.settings
        s.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.watcher = FolderWatcher(s.incoming_dir, s.poll_interval_s, self._on_file, self._on_unsupported)
        self.watcher.start()
        log.info("folder monitor started", extra={"dir": str(s.incoming_dir), "interval_s": s.poll_interval_s})
        return True

    def _on_file(self, path: Path) -> None:
        self.submit_path(path)

    def _on_unsupported(self, path: Path) -> None:
        self.ctx.repo.add_event("DETECTED", "REJECTED", file_path=str(path), detail={"reason": f"unsupported type {path.suffix}"})

    def shutdown(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        if self._lock_conn is not None and self.ctx.pool is not None:
            try:
                self._lock_conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
                self.ctx.pool.putconn(self._lock_conn)
            except Exception:  # noqa: BLE001
                log.debug("advisory unlock failed", exc_info=True)
