"""All SQL for the audit schema lives here (parameterised, psycopg3). Role: agent_rw.

Two-write lifecycle (business data):
  * `ingest_translation()`  SQL Write #1: one INSERT of the ingestion evidence (text + checksums), one transaction.
  * `finalize_invoice()`    SQL Write #2: UPDATE + line items + discrepancies + reports + feedback, ONE transaction.
Operational/audit LOG tables (processing_events, human_feedback, review_decisions) are append-only.
Grants: see infra/postgres/init/03_audit_schema.sql. The erp schema is never touched.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from psycopg import errors as pg_errors
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.normalize import clean_text, jsonable
from app.schemas.lifecycle import DecisionItem, FeedbackItem, Finalization, Ingestion

MAX_HUMAN_CHECKS_HARD = 4  # DB CHECK ceiling: 3 revalidation rounds + the final (4th) check


class DuplicateInvoice(Exception):
    """The raw file or its normalised translated content is already in the audit trail."""

    def __init__(self, kind: str, existing_invoice_id: str | None):
        super().__init__(f"duplicate {kind}: {existing_invoice_id}")
        self.kind = kind  # "file" | "content"
        self.existing_invoice_id = existing_invoice_id


class InvoiceNotFound(LookupError):
    pass


class AlreadyFinalized(Exception):
    def __init__(self, invoice_id: str, status: str):
        super().__init__(f"invoice {invoice_id} is already finalized ({status})")
        self.invoice_id, self.status = invoice_id, status


class ReviewConflict(Exception):
    """The persisted human_check_count does not match what this request expected (concurrent review)."""


class ReviewLimitReached(Exception):
    pass


class AuditRepo:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    # ---- events (operational log) --------------------------------------------
    def add_event(self, stage: str, state: str, *, invoice_id: str | None = None, file_path: str | None = None,
                  file_checksum: str | None = None, detail: dict | None = None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit.processing_events (invoice_id, file_path, file_checksum, stage, state, detail)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (invoice_id, file_path, file_checksum, stage, state, Jsonb(jsonable(detail)) if detail else None),
            )

    def events(self, invoice_id: str) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT stage, state, detail, created_at FROM audit.processing_events WHERE invoice_id=%s ORDER BY id",
                (invoice_id,),
            ).fetchall()

    def event_exists(self, file_checksum: str, state: str, file_path: str) -> bool:
        with self.pool.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM audit.processing_events WHERE file_checksum=%s AND state=%s AND file_path=%s LIMIT 1",
                (file_checksum, state, file_path),
            ).fetchone() is not None

    # ---- duplicate detection -------------------------------------------------
    def find_by_checksum(self, checksum: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT invoice_id FROM audit.invoice_audit WHERE file_checksum=%s", (checksum,)).fetchone()
        return str(row["invoice_id"]) if row else None

    def find_by_content_checksum(self, checksum: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT invoice_id FROM audit.invoice_audit WHERE content_checksum=%s", (checksum,)).fetchone()
        return str(row["invoice_id"]) if row else None

    # ---- SQL Write #1: post-translation ingestion ----------------------------
    def ingest_translation(self, data: Ingestion) -> None:
        """Atomically record the ingestion evidence. Raises DuplicateInvoice if a UNIQUE checksum already exists
        (a concurrent twin got there first); nothing is written in that case."""
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO audit.invoice_audit (invoice_id, file_path, file_checksum, content_checksum,"
                    " extraction_method, raw_extracted_text, translated_text, source_language, translation_confidence,"
                    " ocr_confidence, thread_id, status, human_check_count)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'INGESTED',0)",
                    (data.invoice_id, data.file_path, data.file_checksum, data.content_checksum, data.extraction_method,
                     clean_text(data.raw_extracted_text), clean_text(data.translated_text), data.source_language[:8],
                     round(data.translation_confidence, 3), data.ocr_confidence, data.invoice_id),
                )
        except pg_errors.UniqueViolation as exc:
            constraint = (exc.diag.constraint_name or "")
            if "content_checksum" in constraint:
                raise DuplicateInvoice("content", self.find_by_content_checksum(data.content_checksum)) from exc
            if "file_checksum" in constraint:
                raise DuplicateInvoice("file", self.find_by_checksum(data.file_checksum)) from exc
            raise

    # ---- reads -----------------------------------------------------------------
    def get_invoice(self, invoice_id: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.invoice_audit WHERE invoice_id=%s", (invoice_id,)).fetchone()

    def get_text(self, invoice_id: str, kind: str) -> str:
        col = {"raw": "raw_extracted_text", "translated": "translated_text"}[kind]
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {col} AS t FROM audit.invoice_audit WHERE invoice_id=%s", (invoice_id,)).fetchone()  # noqa: S608 - col from whitelist
        return (row["t"] or "") if row else ""

    def discrepancies(self, invoice_id: str, run: int | None = None) -> list[dict]:
        with self.pool.connection() as conn:
            if run is None:
                return conn.execute("SELECT * FROM audit.discrepancies WHERE invoice_id=%s ORDER BY id", (invoice_id,)).fetchall()
            return conn.execute(
                "SELECT * FROM audit.discrepancies WHERE invoice_id=%s AND validation_run=%s ORDER BY id", (invoice_id, run)
            ).fetchall()

    def line_items(self, invoice_id: str) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.invoice_line_items WHERE invoice_id=%s ORDER BY line_number, id",
                                (invoice_id,)).fetchall()

    def latest_report(self, invoice_id: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT report FROM audit.validation_reports WHERE invoice_id=%s ORDER BY validation_run DESC LIMIT 1",
                               (invoice_id,)).fetchone()
        return row["report"] if row else None

    def feedback_rows(self, invoice_id: str) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.human_feedback WHERE invoice_id=%s ORDER BY id", (invoice_id,)).fetchall()

    # ---- human review log (append-only audit trail) -------------------------------
    def log_review_round(self, invoice_id: str, *, new_count: int, decision: DecisionItem, feedback: list[FeedbackItem]) -> bool:
        """Persist one reject_and_revalidate round: bump human_check_count and append the decision + corrections.

        One transaction, serialised on the invoice row (`FOR UPDATE`) so concurrent reviews cannot exceed the ceiling.
        Idempotent: replaying an already-logged round (count already == new_count) writes nothing and returns False.
        """
        with self.pool.connection() as conn:
            row = conn.execute("SELECT status, human_check_count FROM audit.invoice_audit WHERE invoice_id=%s FOR UPDATE",
                               (invoice_id,)).fetchone()
            if row is None:
                raise InvoiceNotFound(invoice_id)
            if row["status"] != "INGESTED":
                raise AlreadyFinalized(invoice_id, row["status"])
            current = int(row["human_check_count"])
            if current == new_count:
                return False  # replay of a round that already committed
            if current != new_count - 1:
                raise ReviewConflict(f"human_check_count is {current}; expected {new_count - 1}")
            if new_count > MAX_HUMAN_CHECKS_HARD:
                raise ReviewLimitReached(f"human_check_count would exceed {MAX_HUMAN_CHECKS_HARD}")
            conn.execute("UPDATE audit.invoice_audit SET human_check_count=%s WHERE invoice_id=%s", (new_count, invoice_id))
            self._insert_decisions(conn, invoice_id, [decision])
            self._insert_feedback(conn, invoice_id, feedback)
        return True

    def mark_round_revalidated(self, invoice_id: str, review_round: int) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE audit.human_feedback SET revalidated=TRUE WHERE invoice_id=%s AND review_round=%s",
                         (invoice_id, review_round))

    @staticmethod
    def _insert_feedback(conn, invoice_id: str, items: list[FeedbackItem]) -> None:
        for f in items:
            conn.execute(
                "INSERT INTO audit.human_feedback (invoice_id, field_name, original_value, corrected_value, corrected_by,"
                " revalidated, review_round) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (invoice_id, review_round, field_name) WHERE review_round > 0 DO NOTHING",
                (invoice_id, f.field_name[:64], f.original_value, f.corrected_value, f.corrected_by[:128], f.revalidated, f.review_round),
            )

    @staticmethod
    def _insert_decisions(conn, invoice_id: str, items: list[DecisionItem]) -> None:
        for d in items:
            conn.execute(
                "INSERT INTO audit.review_decisions (invoice_id, review_round, decision, reviewer, comments, human_check_count)"
                " VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (invoice_id, review_round) DO NOTHING",
                (invoice_id, d.review_round, d.decision, d.reviewer[:128], d.comments, d.human_check_count),
            )

    # ---- SQL Write #2: finalization ------------------------------------------------
    def finalize_invoice(self, invoice_id: str, fin: Finalization) -> bool:
        """Atomically finalize: invoice fields, immutable AI report, line items, discrepancies (all runs), per-run
        reports, and any human feedback/decisions not inserted yet. ONE transaction: any error rolls everything back.

        Returns False (writes nothing) when the invoice was already finalized, so retries are idempotent.
        """
        with self.pool.connection() as conn:
            row = conn.execute("SELECT status, human_check_count FROM audit.invoice_audit WHERE invoice_id=%s FOR UPDATE",
                               (invoice_id,)).fetchone()
            if row is None:
                raise InvoiceNotFound(invoice_id)
            if row["status"] != "INGESTED":
                return False
            current = int(row["human_check_count"])
            if not (fin.human_check_count - 1 <= current <= fin.human_check_count) or fin.human_check_count > MAX_HUMAN_CHECKS_HARD:
                raise ReviewConflict(f"human_check_count is {current}; finalizing with {fin.human_check_count}")
            inv = fin.invoice
            conn.execute(
                "UPDATE audit.invoice_audit SET invoice_number=%s, vendor_name=%s, vendor_id=%s, po_number=%s, invoice_date=%s,"
                " currency=%s, total_amount=%s, validation_status=%s, recommendation=%s, validation_run=%s, extracted_raw=%s,"
                " report_path=%s, ai_audit_report=%s, status=%s, human_check_count=%s, finalized_at=now() WHERE invoice_id=%s",
                (inv.invoice_number and inv.invoice_number[:64], inv.vendor_name and inv.vendor_name[:255],
                 fin.vendor_id or inv.vendor_id, inv.po_number and inv.po_number[:64], inv.invoice_date,
                 inv.currency, inv.total_amount, fin.validation_status, fin.recommendation, fin.validation_run,
                 Jsonb(jsonable({"extras": inv.extras, "subtotal": inv.subtotal, "tax_amount": inv.tax_amount,
                                 "tax_rate": inv.tax_rate, "vendor_id": fin.vendor_id or inv.vendor_id})),
                 fin.report_path, canonical_json(fin.ai_audit_report), fin.status, fin.human_check_count, invoice_id),
            )
            for n, li in enumerate(inv.line_items, start=1):
                conn.execute(
                    "INSERT INTO audit.invoice_line_items (invoice_id, line_number, item_code, description, quantity,"
                    " unit_price, line_total) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (invoice_id, li.line_number or n, li.item_code and li.item_code[:64], li.description,
                     li.quantity, li.unit_price, li.line_total),
                )
            for run, rows in sorted(fin.discrepancy_runs.items()):
                for d in rows:
                    conn.execute(
                        "INSERT INTO audit.discrepancies (invoice_id, validation_run, superseded, field_name, invoice_value,"
                        " erp_value, deviation_pct, severity) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                        (invoice_id, run, run != fin.validation_run, d.field_name[:64], d.invoice_value, d.erp_value,
                         d.deviation_pct, d.severity),
                    )
            for run, report in sorted(fin.run_reports.items()):
                conn.execute(
                    "INSERT INTO audit.validation_reports (invoice_id, validation_run, report) VALUES (%s,%s,%s)"
                    " ON CONFLICT (invoice_id, validation_run) DO NOTHING",
                    (invoice_id, run, Jsonb(jsonable(report))),
                )
            self._insert_feedback(conn, invoice_id, fin.feedback)
            self._insert_decisions(conn, invoice_id, fin.decisions)
        return True

    # ---- RAG evaluations (RAGAS) ---------------------------------------------
    def create_rag_eval(self, question: str, answer: str, route: str, invoice_id: str | None = None) -> str:
        eid = str(uuid.uuid4())
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit.rag_evaluations (id, invoice_id, question, answer, route, state) VALUES (%s,%s,%s,%s,%s,'pending')",
                (eid, invoice_id, question, answer, route),
            )
        return eid

    def save_rag_scores(self, eval_id: str, *, context_relevance: float, faithfulness: float, answer_relevance: float,
                        low_confidence: bool) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE audit.rag_evaluations SET context_relevance=%s, faithfulness=%s, answer_relevance=%s,"
                " low_confidence=%s, state='done', completed_at=now() WHERE id=%s",
                (round(context_relevance, 3), round(faithfulness, 3), round(answer_relevance, 3), low_confidence, eval_id),
            )

    def fail_rag_eval(self, eval_id: str, error: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("UPDATE audit.rag_evaluations SET state='failed', error=%s, completed_at=now() WHERE id=%s",
                         (error[:500], eval_id))

    def get_rag_eval(self, eval_id: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.rag_evaluations WHERE id=%s", (eval_id,)).fetchone()

    # ---- reindex source -----------------------------------------------------
    def all_invoices_for_index(self, invoice_id: str | None = None) -> list[dict]:
        with self.pool.connection() as conn:
            if invoice_id:
                return conn.execute(
                    "SELECT invoice_id, invoice_number, vendor_name, source_language, translated_text, raw_extracted_text,"
                    " report_path, recommendation, validation_status, validation_run, status FROM audit.invoice_audit WHERE invoice_id=%s",
                    (invoice_id,),
                ).fetchall()
            return conn.execute(
                "SELECT invoice_id, invoice_number, vendor_name, source_language, translated_text, raw_extracted_text,"
                " report_path, recommendation, validation_status, validation_run, status FROM audit.invoice_audit ORDER BY processed_at"
            ).fetchall()

    # ---- whitelisted structured-query templates (RAG router; never LLM-written SQL) ----
    def run_template(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


def to_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False)


def canonical_json(obj: Any) -> str:
    """Stable serialisation of a report for the immutable ai_audit_report column."""
    return json.dumps(jsonable(obj), ensure_ascii=False, sort_keys=True)
