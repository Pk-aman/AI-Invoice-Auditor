from sqlalchemy import Connection, text


def summary(conn: Connection) -> dict:
    total = conn.execute(text("SELECT count(*) FROM audit.invoice_audit")).scalar_one()
    by_rec = {
        r[0] or "pending": r[1]
        for r in conn.execute(text("SELECT recommendation, count(*) FROM audit.invoice_audit GROUP BY 1"))
    }
    by_status = {
        r[0] or "pending": r[1]
        for r in conn.execute(text("SELECT validation_status, count(*) FROM audit.invoice_audit GROUP BY 1"))
    }
    avg = conn.execute(text("SELECT avg(translation_confidence) FROM audit.invoice_audit")).scalar()
    pending_corr = conn.execute(
        text("SELECT count(*) FROM audit.human_feedback WHERE revalidated IS NOT TRUE")
    ).scalar_one()
    return {
        "total_invoices": total,
        "approved": by_rec.get("approve", 0),
        "needs_review": by_rec.get("review", 0),
        "rejected": by_rec.get("reject", 0),
        "pending": by_rec.get("pending", 0),
        "by_recommendation": by_rec,
        "by_validation_status": by_status,
        "avg_translation_confidence": float(avg) if avg is not None else None,
        "pending_corrections": pending_corr,
    }


def by_vendor(conn: Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT COALESCE(a.vendor_name,'(unknown)') AS vendor_name, count(DISTINCT a.invoice_id) AS invoice_count, "
            "count(d.id) FILTER (WHERE d.superseded IS FALSE) AS discrepancy_count "
            "FROM audit.invoice_audit a LEFT JOIN audit.discrepancies d ON d.invoice_id=a.invoice_id "
            "GROUP BY 1 ORDER BY discrepancy_count DESC, invoice_count DESC LIMIT :n"
        ),
        {"n": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def by_day(conn: Connection, days: int) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT processed_at::date AS day, count(*) AS invoice_count, "
            "count(*) FILTER (WHERE recommendation='approve') AS approved, "
            "count(*) FILTER (WHERE recommendation='review')  AS needs_review, "
            "count(*) FILTER (WHERE recommendation='reject')  AS rejected "
            "FROM audit.invoice_audit WHERE processed_at >= now() - make_interval(days => :d) "
            "GROUP BY 1 ORDER BY 1"
        ),
        {"d": days},
    ).mappings().all()
    return [dict(r) for r in rows]


def discrepancy_types(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT field_name, severity, count(*) AS count FROM audit.discrepancies "
            "WHERE superseded IS FALSE GROUP BY 1,2 ORDER BY count DESC, field_name"
        )
    ).mappings().all()
    return [dict(r) for r in rows]


def currencies(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            "SELECT COALESCE(currency,'(unknown)') AS currency, count(*) AS invoice_count, "
            "COALESCE(sum(total_amount),0) AS total_amount FROM audit.invoice_audit GROUP BY 1 ORDER BY 2 DESC"
        )
    ).mappings().all()
    return [dict(r) for r in rows]
