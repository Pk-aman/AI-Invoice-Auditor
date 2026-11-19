"""Human-in-the-loop review: validates a reviewer's submission and resumes the paused LangGraph run.

Concurrency: one review at a time per invoice (PostgreSQL advisory lock on a dedicated connection), plus the
row-level `FOR UPDATE` + expected-count check inside the repository transactions as the backstop. A second request
for the same invoice waits, then sees the state the first one left (e.g. already finalized -> 409).
"""
from __future__ import annotations

import contextlib
import logging
import time
import zlib
from collections.abc import Iterator

from langgraph.types import Command

from app.agents.review import ReviewError, resolve_corrections
from app.api.schemas import ReviewRequest, ReviewResponse, ReviewState
from app.core.context import AppContext
from app.core.observability import traced_span
from app.persistence.audit_repo import AlreadyFinalized, InvoiceNotFound, ReviewConflict, ReviewLimitReached
from app.schemas.models import ExtractedInvoice
from app.services.pipeline import Pipeline

log = logging.getLogger(__name__)

FINAL_STATUSES = {"APPROVED", "REJECTED", "SKIPPED_MAX_RETRIES", "LEGACY_FINALIZED"}
_LOCK_POLL_S = 0.05


class ReviewBusy(ReviewError):
    def __init__(self):
        super().__init__("Another review of this invoice is still in progress. Please try again in a moment.", 409, "review_busy")


class ReviewService:
    def __init__(self, ctx: AppContext, pipeline: Pipeline):
        self.ctx = ctx
        self.pipeline = pipeline

    # ---- locking -------------------------------------------------------------------------------
    @contextlib.contextmanager
    def _lock(self, invoice_id: str) -> Iterator[None]:
        pool = self.ctx.pool
        key = zlib.crc32(f"review:{invoice_id}".encode())
        deadline = time.monotonic() + self.ctx.settings.review_lock_timeout_s
        conn = pool.getconn()
        conn.autocommit = True
        try:
            while not conn.execute("SELECT pg_try_advisory_lock(%s) AS got", (key,)).fetchone()["got"]:
                if time.monotonic() >= deadline:
                    raise ReviewBusy()
                time.sleep(_LOCK_POLL_S)
            try:
                yield
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
        finally:
            conn.autocommit = False
            pool.putconn(conn)

    # ---- helpers -------------------------------------------------------------------------------
    def _checkpoint(self, invoice_id: str):
        return self.pipeline.invoice_graph.get_state(self.pipeline.config(invoice_id))

    def _check_reviewer(self, reviewer: str) -> None:
        allowed = self.ctx.settings.reviewer_allow_list
        if allowed and reviewer.lower() not in allowed:
            raise ReviewError("This reviewer is not authorised to review invoices.", 403, "reviewer_not_authorised")

    # ---- POST /invoices/{id}/review ------------------------------------------------------------
    def review(self, invoice_id: str, req: ReviewRequest) -> ReviewResponse:
        self._check_reviewer(req.reviewer)
        with self._lock(invoice_id), traced_span("invoice_review", invoice_id=invoice_id, decision=req.decision):
            row = self.ctx.repo.get_invoice(invoice_id)
            if row is None:
                raise ReviewError("Invoice not found.", 404, "invoice_not_found")
            if row["status"] != "INGESTED":
                raise ReviewError(f"This invoice has already been finalized ({row['status']}).", 409, "already_finalized")
            snap = self._checkpoint(invoice_id)
            if not snap.values or tuple(snap.next) != ("human_review",):
                raise ReviewError("This invoice is not waiting for review yet. If processing stopped, retry it first.",
                                  409, "not_awaiting_review")
            state = snap.values
            if req.decision == "approve" and req.field_corrections:
                raise ReviewError("Approve cannot carry corrections. Use reject_and_revalidate to correct values.", 422, "corrections_not_allowed")
            if req.decision == "reject_and_revalidate" and not req.field_corrections:
                raise ReviewError("reject_and_revalidate needs at least one field correction. "
                                  "Use reject to reject the invoice without corrections.", 422, "corrections_required")
            resolved = resolve_corrections(ExtractedInvoice.model_validate(state["invoice"]), req.field_corrections)
            payload = {
                "decision": req.decision, "reviewer": req.reviewer, "comments": req.comments,
                "corrections": [{"field_name": c.field_name, "original_value": c.original_value,
                                 "corrected_value": c.corrected_value} for c in resolved],
            }
            try:
                final = self.pipeline.invoice_graph.invoke(Command(resume=payload), self.pipeline.config(invoice_id))
            except (AlreadyFinalized, ReviewLimitReached) as exc:
                raise ReviewError(str(exc), 409, "already_finalized") from exc
            except ReviewConflict as exc:
                raise ReviewError(str(exc), 409, "review_conflict") from exc
            except InvoiceNotFound as exc:
                raise ReviewError("Invoice not found.", 404, "invoice_not_found") from exc
            except Exception as exc:  # noqa: BLE001 - LLM/ERP/DB failure mid-run: the checkpoint is kept, retry resumes
                log.exception("review run failed", extra={"invoice_id": invoice_id})
                raise ReviewError("The review could not be completed because a step failed. "
                                  f"Nothing was lost: retry the invoice to resume ({type(exc).__name__}).", 502, "review_failed") from exc
            return self._response(invoice_id, final)

    def _response(self, invoice_id: str, values: dict) -> ReviewResponse:
        latest = values.get("discrepancy_runs", {}).get(str(values.get("validation_run", 1)), [])
        status = values.get("status") or "NEEDS_REPORT_REVIEW"
        return ReviewResponse(
            invoice_id=invoice_id, human_check_count=values.get("human_check_count", 0), status=status,
            validation_status=values["validation_status"], recommendation=values["recommendation"],
            discrepancies_count=len(latest), validation_run=values.get("validation_run"), warnings=values.get("warnings") or [])

    # ---- POST /invoices/{id}/retry --------------------------------------------------------------
    def retry(self, invoice_id: str) -> None:
        row = self.ctx.repo.get_invoice(invoice_id)
        if row is None:
            raise ReviewError("Invoice not found.", 404, "invoice_not_found")
        if row["status"] != "INGESTED":
            raise ReviewError(f"This invoice has already been finalized ({row['status']}).", 409, "already_finalized")
        nxt = self.pipeline.pending_nodes(invoice_id)
        if not nxt or nxt == ("human_review",):
            raise ReviewError("Nothing to retry: this invoice is waiting for review or has no interrupted run.", 409, "nothing_to_retry")
        try:
            self.pipeline.submit_retry(invoice_id)
        except RuntimeError as exc:
            raise ReviewError(str(exc), 409, "already_running") from exc

    # ---- GET /invoices/{id}/review ----------------------------------------------------------------
    def state(self, invoice_id: str) -> ReviewState:
        s = self.ctx.settings
        row = self.ctx.repo.get_invoice(invoice_id)
        if row is None:
            raise ReviewError("Invoice not found.", 404, "invoice_not_found")
        if row["status"] != "INGESTED":
            return self._final_state(invoice_id, row)
        snap = self._checkpoint(invoice_id)
        values = snap.values or {}
        waiting = tuple(snap.next) == ("human_review",)
        count = int(row["human_check_count"])
        return ReviewState(
            invoice_id=invoice_id, status="NEEDS_REPORT_REVIEW" if waiting else "PROCESSING", awaiting_review=waiting,
            human_check_count=count, max_human_checks=s.max_human_checks, remaining_checks=max(0, s.max_human_checks + 1 - count),
            validation_run=values.get("validation_run"), validation_status=values.get("validation_status"),
            recommendation=values.get("recommendation"), invoice=values.get("invoice"),
            corrections=values.get("corrections") or {}, report=values.get("report"), ai_audit_report=values.get("original_report"),
            warnings=values.get("warnings") or [])

    def _final_state(self, invoice_id: str, row: dict) -> ReviewState:
        import json

        s, repo = self.ctx.settings, self.ctx.repo
        lines = repo.line_items(invoice_id)
        invoice = {
            "invoice_number": row["invoice_number"], "vendor_name": row["vendor_name"], "po_number": row["po_number"],
            "invoice_date": row["invoice_date"].isoformat() if row["invoice_date"] else None, "currency": row["currency"],
            "total_amount": str(row["total_amount"]) if row["total_amount"] is not None else None,
            "line_items": [{"line_number": ln["line_number"], "item_code": ln["item_code"], "description": ln["description"],
                            "quantity": str(ln["quantity"]) if ln["quantity"] is not None else None,
                            "unit_price": str(ln["unit_price"]) if ln["unit_price"] is not None else None,
                            "line_total": str(ln["line_total"]) if ln["line_total"] is not None else None} for ln in lines],
        }
        feedback: dict[str, str] = {}
        for fb in repo.feedback_rows(invoice_id):
            feedback[fb["field_name"]] = fb["corrected_value"]
        count = int(row["human_check_count"])
        return ReviewState(
            invoice_id=invoice_id, status=row["status"], awaiting_review=False, human_check_count=count,
            max_human_checks=s.max_human_checks, remaining_checks=0, validation_run=row["validation_run"],
            validation_status=row["validation_status"], recommendation=row["recommendation"], invoice=invoice,
            corrections=feedback, report=repo.latest_report(invoice_id),
            ai_audit_report=json.loads(row["ai_audit_report"]) if row["ai_audit_report"] else None)
