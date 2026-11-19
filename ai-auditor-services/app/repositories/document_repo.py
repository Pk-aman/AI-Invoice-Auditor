"""Per-file view of the pipeline (one row per uploaded/detected document, whatever its state)."""
from typing import Any

from sqlalchemy import Connection, text

_BASE = """
WITH ev AS (
  SELECT file_checksum,
         min(created_at) FILTER (WHERE state <> 'DUPLICATE')                          AS uploaded_at,
         (array_agg(detail->>'original_filename' ORDER BY id)
            FILTER (WHERE detail->>'original_filename' IS NOT NULL AND state <> 'DUPLICATE'))[1] AS orig_name,
         (array_agg(file_path ORDER BY id) FILTER (WHERE file_path IS NOT NULL AND state <> 'DUPLICATE'))[1] AS path,
         bool_or(stage = 'COMPLETED' AND state = 'OK')                                AS completed,
         bool_or(stage <> 'QUEUED' AND state <> 'DUPLICATE')                          AS picked_up,
         (array_agg(state ORDER BY id DESC) FILTER (WHERE state <> 'DUPLICATE'))[1]   AS last_state,
         (array_agg(stage || ':' || state ORDER BY id DESC) FILTER (WHERE state <> 'DUPLICATE'))[1] AS last_event,
         (array_agg(coalesce(detail->>'error', detail->>'reason') ORDER BY id DESC)
            FILTER (WHERE coalesce(detail->>'error', detail->>'reason') IS NOT NULL AND state <> 'DUPLICATE'))[1] AS last_error,
         bool_or(stage = 'DEDUPE' AND state = 'DUPLICATE')                            AS content_duplicate,
         (array_agg(detail->>'existing_invoice_id' ORDER BY id)
            FILTER (WHERE stage = 'DEDUPE' AND state = 'DUPLICATE'))[1]               AS duplicate_of
  FROM audit.processing_events
  WHERE file_checksum IS NOT NULL
  GROUP BY file_checksum
  HAVING bool_or(state <> 'DUPLICATE')
), doc AS (
  SELECT ev.file_checksum AS checksum,
         left(ev.file_checksum, 16) AS document_id,
         coalesce(ev.orig_name, regexp_replace(ev.path, '^.*/', '')) AS filename,
         ev.uploaded_at, ev.path,
         CASE WHEN ev.content_duplicate THEN 'duplicate'
              WHEN ev.completed THEN 'audited'
              WHEN a.status = 'INGESTED' AND ev.last_event = 'HUMAN_REVIEW:PENDING' THEN 'review'
              WHEN ev.last_state IN ('FAILED','REJECTED') THEN 'failed'
              WHEN NOT ev.picked_up THEN 'awaiting'
              ELSE 'processing' END AS processing_status,
         CASE WHEN ev.content_duplicate OR NOT ev.completed THEN 'not_available'
              WHEN a.status = 'APPROVED' THEN 'approved'
              WHEN a.status = 'REJECTED' THEN 'rejected'
              WHEN a.status = 'SKIPPED_MAX_RETRIES' THEN 'needs_attention'
              WHEN a.recommendation = 'approve' AND a.validation_status = 'passed'
                   AND NOT EXISTS (SELECT 1 FROM audit.discrepancies d
                                   WHERE d.invoice_id = a.invoice_id AND NOT d.superseded)
              THEN 'no_issues'
              ELSE 'needs_attention' END AS audit_result,
         CASE WHEN ev.last_state IN ('FAILED','REJECTED') AND NOT ev.completed THEN ev.last_error END AS error,
         CASE WHEN ev.content_duplicate THEN ev.duplicate_of::uuid END AS duplicate_of,
         a.invoice_id, a.invoice_number, a.vendor_name, a.currency, a.total_amount,
         a.recommendation, a.validation_status, a.status, a.human_check_count
  FROM ev LEFT JOIN audit.invoice_audit a ON a.file_checksum = ev.file_checksum
)
"""


def _where(q, processing_status, audit_result, within_days) -> tuple[str, dict[str, Any]]:
    clauses, p = [], {}
    if q:
        clauses.append("(invoice_number ILIKE :q OR filename ILIKE :q OR vendor_name ILIKE :q)")
        p["q"] = f"%{q}%"
    if processing_status:
        clauses.append("processing_status = :ps")
        p["ps"] = processing_status
    if audit_result:
        clauses.append("audit_result = :ar")
        p["ar"] = audit_result
    if within_days:
        clauses.append("uploaded_at >= now() - make_interval(days => :days)")
        p["days"] = within_days
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", p


def list_documents(conn: Connection, *, q, processing_status, audit_result, within_days, page, page_size):
    where, p = _where(q, processing_status, audit_result, within_days)
    total = conn.execute(text(_BASE + f"SELECT count(*) FROM doc{where}"), p).scalar_one()
    rows = conn.execute(
        text(_BASE + f"SELECT * FROM doc{where} ORDER BY uploaded_at DESC LIMIT :lim OFFSET :off"),
        {**p, "lim": page_size, "off": (page - 1) * page_size},
    ).mappings().all()
    return [dict(r) for r in rows], total


def stats(conn: Connection) -> dict[str, int]:
    r = conn.execute(text(_BASE + """
        SELECT count(*) AS total,
               count(*) FILTER (WHERE processing_status = 'processing') AS processing,
               count(*) FILTER (WHERE processing_status = 'review')     AS awaiting_review,
               count(*) FILTER (WHERE processing_status = 'audited')    AS audited,
               count(*) FILTER (WHERE audit_result = 'needs_attention') AS needs_attention,
               count(*) FILTER (WHERE processing_status = 'awaiting')   AS awaiting,
               count(*) FILTER (WHERE processing_status = 'failed')     AS failed
        FROM doc""")).mappings().one()
    return {k: int(v) for k, v in r.items()}


def get_document(conn: Connection, document_id: str) -> dict | None:
    r = conn.execute(text(_BASE + "SELECT * FROM doc WHERE document_id = :d"), {"d": document_id}).mappings().first()
    return dict(r) if r else None
