import uuid
from typing import Any

from sqlalchemy import Connection, text

SORTABLE = {
    "processed_at": "processed_at",
    "invoice_date": "invoice_date",
    "total_amount": "total_amount",
    "vendor_name": "vendor_name",
    "invoice_number": "invoice_number",
}

SUMMARY_COLS = (
    "invoice_id, invoice_number, vendor_name, vendor_id, po_number, invoice_date, currency, total_amount, "
    "validation_status, recommendation, source_language, translation_confidence, validation_run, status, "
    "human_check_count, processed_at"
)


def list_invoices(
    conn: Connection,
    *,
    status: str | None,
    recommendation: str | None,
    vendor: str | None,
    currency: str | None,
    date_from,
    date_to,
    q: str | None,
    page: int,
    page_size: int,
    sort: str,
    order: str,
) -> tuple[list[dict], int]:
    where, params = ["1=1"], {}
    if status:
        where.append("validation_status = :status")
        params["status"] = status
    if recommendation:
        where.append("recommendation = :rec")
        params["rec"] = recommendation
    if vendor:
        where.append("vendor_name ILIKE :vendor")
        params["vendor"] = f"%{vendor}%"
    if currency:
        where.append("currency = :cur")
        params["cur"] = currency.upper()
    if date_from:
        where.append("processed_at::date >= :dfrom")
        params["dfrom"] = date_from
    if date_to:
        where.append("processed_at::date <= :dto")
        params["dto"] = date_to
    if q:
        where.append("(invoice_number ILIKE :q OR vendor_name ILIKE :q)")
        params["q"] = f"%{q}%"
    clause = " AND ".join(where)
    col = SORTABLE.get(sort, "processed_at")
    direction = "ASC" if order.lower() == "asc" else "DESC"
    total = conn.execute(text(f"SELECT count(*) FROM audit.invoice_audit WHERE {clause}"), params).scalar_one()
    rows = conn.execute(
        text(
            f"SELECT {SUMMARY_COLS} FROM audit.invoice_audit WHERE {clause} "
            f"ORDER BY {col} {direction} NULLS LAST, invoice_id LIMIT :lim OFFSET :off"
        ),
        {**params, "lim": page_size, "off": (page - 1) * page_size},
    ).mappings().all()
    return [dict(r) for r in rows], total


def get_invoice(conn: Connection, invoice_id: uuid.UUID) -> dict | None:
    row = conn.execute(
        text(
            f"SELECT {SUMMARY_COLS}, file_path, file_checksum, extraction_method, ocr_confidence, "
            "extracted_raw, content_checksum, finalized_at, (ai_audit_report IS NOT NULL) AS ai_report_available "
            "FROM audit.invoice_audit WHERE invoice_id = :i"
        ),
        {"i": invoice_id},
    ).mappings().first()
    return dict(row) if row else None


def invoice_exists(conn: Connection, invoice_id: uuid.UUID) -> bool:
    return bool(conn.execute(text("SELECT 1 FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}).first())


def line_items(conn: Connection, invoice_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, line_number, item_code, description, quantity, unit_price, line_total "
            "FROM audit.invoice_line_items WHERE invoice_id=:i ORDER BY line_number, id"
        ),
        {"i": invoice_id},
    ).mappings().all()
    return [dict(r) for r in rows]


def discrepancies(conn: Connection, invoice_id: uuid.UUID, run: str) -> list[dict]:
    sql = (
        "SELECT id, validation_run, superseded, field_name, invoice_value, erp_value, deviation_pct, "
        "severity, detected_at FROM audit.discrepancies WHERE invoice_id=:i "
    )
    if run == "latest":
        sql += (
            "AND validation_run = (SELECT validation_run FROM audit.invoice_audit WHERE invoice_id=:i) "
        )
    sql += "ORDER BY validation_run DESC, id"
    return [dict(r) for r in conn.execute(text(sql), {"i": invoice_id}).mappings().all()]


def get_text(conn: Connection, invoice_id: uuid.UUID, kind: str) -> tuple[bool, str | None]:
    col = "raw_extracted_text" if kind == "raw" else "translated_text"
    row = conn.execute(
        text(f"SELECT {col} AS t FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}
    ).mappings().first()
    return (row is not None, row["t"] if row else None)


def events_for_invoice(conn: Connection, invoice_id: uuid.UUID) -> list[dict]:
    checksum = conn.execute(
        text("SELECT file_checksum FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}
    ).scalar()
    rows = conn.execute(
        text(
            "SELECT id, invoice_id, file_path, file_checksum, stage, state, detail, created_at "
            "FROM audit.processing_events WHERE invoice_id=:i OR (file_checksum IS NOT NULL AND file_checksum=:c) "
            "ORDER BY created_at, id"
        ),
        {"i": invoice_id, "c": checksum},
    ).mappings().all()
    return [dict(r) for r in rows]


def find_invoice_by_checksum(conn: Connection, checksum: str) -> uuid.UUID | None:
    return conn.execute(
        text("SELECT invoice_id FROM audit.invoice_audit WHERE file_checksum=:c"), {"c": checksum}
    ).scalar()


def events_by_checksum(conn: Connection, checksum: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT id, invoice_id, stage, state, detail, created_at FROM audit.processing_events "
            "WHERE file_checksum=:c ORDER BY id"
        ),
        {"c": checksum},
    ).mappings().all()
    return [dict(r) for r in rows]


def insert_event(conn: Connection, *, file_path, checksum, stage, state, detail) -> None:
    import json

    conn.execute(
        text(
            "INSERT INTO audit.processing_events (file_path, file_checksum, stage, state, detail) "
            "VALUES (:p, :c, :s, :st, CAST(:d AS jsonb))"
        ),
        {"p": file_path, "c": checksum, "s": stage, "st": state, "d": json.dumps(detail)},
    )


def ai_audit_report(conn: Connection, invoice_id: uuid.UUID) -> tuple[bool, str | None]:
    """(invoice exists, the immutable run-1 AI report exactly as stored in PostgreSQL)."""
    row = conn.execute(text("SELECT ai_audit_report FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}).first()
    return (row is not None, row[0] if row else None)


def invoice_status(conn: Connection, invoice_id: uuid.UUID) -> str | None:
    return conn.execute(text("SELECT status FROM audit.invoice_audit WHERE invoice_id=:i"), {"i": invoice_id}).scalar()


def review_decisions(conn: Connection, invoice_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        text("SELECT id, invoice_id, review_round, decision, reviewer, comments, human_check_count, decided_at "
             "FROM audit.review_decisions WHERE invoice_id=:i ORDER BY review_round"),
        {"i": invoice_id},
    ).mappings().all()
    return [dict(r) for r in rows]
