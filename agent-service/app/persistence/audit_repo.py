"""All SQL for the audit schema lives here (parameterised, psycopg3). Role: agent_rw.

Grants (see infra/postgres/init/04_audit_schema.sql): SELECT/INSERT/UPDATE on pipeline tables, no DELETE,
SELECT + UPDATE(revalidated) on human_feedback. The erp schema is never touched.
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.core.normalize import clean_text, jsonable
from app.schemas.models import Discrepancy, ExtractedInvoice, LineItem


class AuditRepo:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    # ---- events -------------------------------------------------------------
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

    # ---- invoice rows -------------------------------------------------------
    def find_by_checksum(self, checksum: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT invoice_id FROM audit.invoice_audit WHERE file_checksum=%s", (checksum,)).fetchone()
        return str(row["invoice_id"]) if row else None

    def create_invoice(self, invoice_id: str, file_path: str, checksum: str, method: str, raw_text: str,
                       ocr_confidence: float | None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit.invoice_audit (invoice_id, file_path, file_checksum, extraction_method,"
                " raw_extracted_text, ocr_confidence, thread_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (invoice_id, file_path, checksum, method, clean_text(raw_text), ocr_confidence, invoice_id),
            )

    def get_invoice(self, invoice_id: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.invoice_audit WHERE invoice_id=%s", (invoice_id,)).fetchone()

    def get_text(self, invoice_id: str, kind: str) -> str:
        col = {"raw": "raw_extracted_text", "translated": "translated_text"}[kind]
        with self.pool.connection() as conn:
            row = conn.execute(f"SELECT {col} AS t FROM audit.invoice_audit WHERE invoice_id=%s", (invoice_id,)).fetchone()  # noqa: S608 - col from whitelist
        return (row["t"] or "") if row else ""

    def save_translation(self, invoice_id: str, translated: str, language: str, confidence: float) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE audit.invoice_audit SET translated_text=%s, source_language=%s, translation_confidence=%s"
                " WHERE invoice_id=%s",
                (clean_text(translated), language[:8], round(confidence, 3), invoice_id),
            )

    def save_parsed(self, invoice_id: str, inv: ExtractedInvoice) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE audit.invoice_audit SET invoice_number=%s, vendor_name=%s, po_number=%s, invoice_date=%s,"
                " currency=%s, total_amount=%s, extracted_raw=%s WHERE invoice_id=%s",
                (inv.invoice_number and inv.invoice_number[:64], inv.vendor_name and inv.vendor_name[:255],
                 inv.po_number and inv.po_number[:64], inv.invoice_date, inv.currency, inv.total_amount,
                 Jsonb(jsonable({"extras": inv.extras, "subtotal": inv.subtotal, "tax_amount": inv.tax_amount,
                                 "tax_rate": inv.tax_rate, "vendor_id": inv.vendor_id})), invoice_id),
            )
            # line items are insert-only for the agent (no DELETE grant): guard against double insert on retry
            have = conn.execute("SELECT count(*) AS n FROM audit.invoice_line_items WHERE invoice_id=%s", (invoice_id,)).fetchone()
            if not have["n"]:
                for n, li in enumerate(inv.line_items, start=1):
                    conn.execute(
                        "INSERT INTO audit.invoice_line_items (invoice_id, line_number, item_code, description,"
                        " quantity, unit_price, line_total) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (invoice_id, li.line_number or n, li.item_code and li.item_code[:64], li.description,
                         li.quantity, li.unit_price, li.line_total),
                    )

    def get_parsed(self, invoice_id: str) -> ExtractedInvoice | None:
        """Original (as extracted) invoice rebuilt from the audit tables."""
        row = self.get_invoice(invoice_id)
        if not row:
            return None
        raw = row["extracted_raw"] or {}
        with self.pool.connection() as conn:
            lines = conn.execute(
                "SELECT line_number, item_code, description, quantity, unit_price, line_total"
                " FROM audit.invoice_line_items WHERE invoice_id=%s ORDER BY line_number, id", (invoice_id,)
            ).fetchall()
        return ExtractedInvoice(
            invoice_number=row["invoice_number"], vendor_name=row["vendor_name"], vendor_id=raw.get("vendor_id"),
            po_number=row["po_number"], invoice_date=row["invoice_date"], currency=row["currency"],
            total_amount=row["total_amount"], subtotal=raw.get("subtotal"), tax_amount=raw.get("tax_amount"),
            tax_rate=raw.get("tax_rate"), extras=raw.get("extras") or {},
            line_items=[LineItem(**ln) for ln in lines],
        )

    # ---- validation output --------------------------------------------------
    def next_validation_run(self, invoice_id: str) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT validation_run FROM audit.invoice_audit WHERE invoice_id=%s", (invoice_id,)).fetchone()
        return int(row["validation_run"]) + 1 if row else 1

    def save_validation(self, invoice_id: str, *, run: int, status: str, recommendation: str,
                        discrepancies: list[Discrepancy], vendor_id: str | None, report_path: str | None) -> None:
        """Persist a validation run. Earlier discrepancy rows are kept and flagged superseded (never deleted)."""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE audit.discrepancies SET superseded=TRUE WHERE invoice_id=%s AND validation_run<%s",
                (invoice_id, run),
            )
            for d in discrepancies:
                conn.execute(
                    "INSERT INTO audit.discrepancies (invoice_id, validation_run, field_name, invoice_value,"
                    " erp_value, deviation_pct, severity) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (invoice_id, run, d.field_name[:64], d.invoice_value, d.erp_value, d.deviation_pct, d.severity),
                )
            conn.execute(
                "UPDATE audit.invoice_audit SET validation_status=%s, recommendation=%s, validation_run=%s,"
                " report_path=%s, vendor_id=COALESCE(%s, vendor_id) WHERE invoice_id=%s",
                (status, recommendation, run, report_path, vendor_id, invoice_id),
            )

    def discrepancies(self, invoice_id: str, run: int | None = None) -> list[dict]:
        with self.pool.connection() as conn:
            if run is None:
                return conn.execute("SELECT * FROM audit.discrepancies WHERE invoice_id=%s ORDER BY id", (invoice_id,)).fetchall()
            return conn.execute(
                "SELECT * FROM audit.discrepancies WHERE invoice_id=%s AND validation_run=%s ORDER BY id", (invoice_id, run)
            ).fetchall()

    # ---- human feedback (read-only for the agent, except the revalidated flag) ----
    def latest_feedback(self, invoice_id: str) -> dict[str, str]:
        """field_name -> latest corrected_value."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (field_name) field_name, corrected_value FROM audit.human_feedback"
                " WHERE invoice_id=%s ORDER BY field_name, id DESC", (invoice_id,)
            ).fetchall()
        return {r["field_name"]: r["corrected_value"] for r in rows}

    def mark_feedback_revalidated(self, invoice_id: str) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE audit.human_feedback SET revalidated=TRUE WHERE invoice_id=%s AND revalidated IS NOT TRUE", (invoice_id,)
            )
            return cur.rowcount

    # ---- RAG evaluations ----------------------------------------------------
    def create_rag_eval(self, question: str, answer: str, route: str) -> str:
        eid = str(uuid.uuid4())
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO audit.rag_evaluations (id, question, answer, route) VALUES (%s,%s,%s,%s)",
                (eid, question, answer, route),
            )
        return eid

    def save_rag_scores(self, eval_id: str, relevance: float, groundedness: float, context_relevance: float) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE audit.rag_evaluations SET relevance=%s, groundedness=%s, context_relevance=%s WHERE id=%s",
                (round(relevance, 3), round(groundedness, 3), round(context_relevance, 3), eval_id),
            )

    def get_rag_eval(self, eval_id: str) -> dict | None:
        with self.pool.connection() as conn:
            return conn.execute("SELECT * FROM audit.rag_evaluations WHERE id=%s", (eval_id,)).fetchone()

    # ---- reindex source -----------------------------------------------------
    def all_invoices_for_index(self, invoice_id: str | None = None) -> list[dict]:
        with self.pool.connection() as conn:
            if invoice_id:
                return conn.execute(
                    "SELECT invoice_id, invoice_number, vendor_name, source_language, translated_text, raw_extracted_text,"
                    " report_path, recommendation, validation_status, validation_run FROM audit.invoice_audit WHERE invoice_id=%s",
                    (invoice_id,),
                ).fetchall()
            return conn.execute(
                "SELECT invoice_id, invoice_number, vendor_name, source_language, translated_text, raw_extracted_text,"
                " report_path, recommendation, validation_status, validation_run FROM audit.invoice_audit ORDER BY processed_at"
            ).fetchall()

    # ---- whitelisted structured-query templates (RAG router; never LLM-written SQL) ----
    def run_template(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


def to_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), ensure_ascii=False)
