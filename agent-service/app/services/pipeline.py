"""Runs the invoice graph: folder monitor thread -> bounded worker pool; resume/retry; singleton guard.

A new invoice runs until the human-review interrupt; `ReviewService` resumes it (see services/review.py)."""
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
from app.core.observability import traced_span
from app.graphs.invoice_graph import build_invoice_graph

log = logging.getLogger(__name__)

_LOCK_KEY = zlib.crc32(b"ai-auditor-monitor")


class Pipeline:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.invoice_graph = build_invoice_graph(ctx, ctx.checkpointer)
        self.executor = ThreadPoolExecutor(max_workers=max(1, ctx.settings.processing_concurrency), thread_name_prefix="invoice")
        self.watcher: FolderWatcher | None = None
        self._lock_conn = None
        self._inflight: set[str] = set()
        self._guard = threading.Lock()

    # ---- synchronous runs (also used by tests) --------------------------------------
    @staticmethod
    def config(invoice_id: str) -> dict:
        # a review loop traverses ~9 nodes per round; the default recursion limit (25) is too small for 3 rounds
        return {"configurable": {"thread_id": invoice_id}, "recursion_limit": 200}

    def process_path(self, path: Path, invoice_id: str | None = None) -> dict:
        iid = invoice_id or str(uuid.uuid4())
        token = correlation_id.set(iid)
        try:
            with traced_span("invoice_pipeline", invoice_id=iid, file=path.name):
                return self.invoice_graph.invoke({"invoice_id": iid, "file_path": str(path), "validation_run": 1},
                                                 self.config(iid))
        finally:
            correlation_id.reset(token)

    def retry_sync(self, invoice_id: str) -> dict:
        """Resume a run that stopped on a failing node (checkpoint kept). Idempotent: completed nodes are not re-run."""
        token = correlation_id.set(invoice_id)
        try:
            with traced_span("invoice_retry", invoice_id=invoice_id):
                return self.invoice_graph.invoke(None, self.config(invoice_id))
        finally:
            correlation_id.reset(token)

    def pending_nodes(self, invoice_id: str) -> tuple[str, ...]:
        """Nodes still to run for this thread ((), ('human_review',) when waiting for a reviewer, or a failed node)."""
        return tuple(self.invoice_graph.get_state(self.config(invoice_id)).next)

    # ---- async submission -------------------------------------------------------------
    def submit_path(self, path: Path, invoice_id: str | None = None) -> str:
        iid = invoice_id or str(uuid.uuid4())
        self.executor.submit(self._safe, self.process_path, path, iid)
        return iid

    def submit_retry(self, invoice_id: str) -> None:
        with self._guard:
            if invoice_id in self._inflight:
                raise RuntimeError("this invoice is already being processed")
            self._inflight.add(invoice_id)
        fut: Future = self.executor.submit(self._safe, self.retry_sync, invoice_id)
        fut.add_done_callback(lambda _f: self._release(invoice_id))

    def _release(self, invoice_id: str) -> None:
        with self._guard:
            self._inflight.discard(invoice_id)

    @staticmethod
    def _safe(fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - background failures must be logged, not lost (the checkpoint keeps the state)
            log.exception("background pipeline run stopped; resumable with POST /invoices/{id}/retry")

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
