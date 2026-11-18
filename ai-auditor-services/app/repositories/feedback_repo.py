import uuid

from sqlalchemy import Connection, text


def latest_corrections(conn: Connection, invoice_id: uuid.UUID) -> dict[str, str | None]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT ON (field_name) field_name, corrected_value FROM audit.human_feedback "
            "WHERE invoice_id=:i ORDER BY field_name, id DESC"
        ),
        {"i": invoice_id},
    ).all()
    return {r[0]: r[1] for r in rows}


def latest_correction(conn: Connection, invoice_id: uuid.UUID, field: str) -> tuple[bool, str | None]:
    row = conn.execute(
        text(
            "SELECT corrected_value FROM audit.human_feedback WHERE invoice_id=:i AND field_name=:f "
            "ORDER BY id DESC LIMIT 1"
        ),
        {"i": invoice_id, "f": field},
    ).first()
    return (row is not None, row[0] if row else None)


def pending_count(conn: Connection, invoice_id: uuid.UUID) -> int:
    return conn.execute(
        text("SELECT count(*) FROM audit.human_feedback WHERE invoice_id=:i AND revalidated IS NOT TRUE"),
        {"i": invoice_id},
    ).scalar_one()


def insert_feedback(conn: Connection, *, invoice_id, field, original, corrected, by) -> dict:
    row = conn.execute(
        text(
            "INSERT INTO audit.human_feedback (invoice_id, field_name, original_value, corrected_value, corrected_by) "
            "VALUES (:i, :f, :o, :c, :b) "
            "RETURNING id, invoice_id, field_name, original_value, corrected_value, corrected_by, corrected_at, revalidated"
        ),
        {"i": invoice_id, "f": field, "o": original, "c": corrected, "b": by},
    ).mappings().one()
    return dict(row)


def history(conn: Connection, invoice_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT id, invoice_id, field_name, original_value, corrected_value, corrected_by, corrected_at, revalidated "
            "FROM audit.human_feedback WHERE invoice_id=:i ORDER BY id DESC"
        ),
        {"i": invoice_id},
    ).mappings().all()
    return [dict(r) for r in rows]
