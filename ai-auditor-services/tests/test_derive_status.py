from datetime import UTC, datetime

from app.services.upload_service import derive_status

NOW = datetime.now(UTC)


def ev(stage, state, **detail):
    return {"stage": stage, "state": state, "detail": detail or None, "created_at": NOW}


def test_status_progression():
    assert derive_status([ev("QUEUED", "OK")], None)["status"] == "QUEUED"
    assert derive_status([ev("QUEUED", "OK"), ev("EXTRACT", "STARTED")], None)["status"] == "PROCESSING"
    assert derive_status([ev("QUEUED", "OK"), ev("INDEX", "OK"), ev("HUMAN_REVIEW", "PENDING")], None)["status"] == "NEEDS_REVIEW"
    assert derive_status([ev("HUMAN_REVIEW", "PENDING"), ev("REVIEW", "STARTED")], None)["status"] == "PROCESSING"
    assert derive_status([ev("HUMAN_REVIEW", "PENDING"), ev("COMPLETED", "OK")], None)["status"] == "COMPLETED"
    failed = derive_status([ev("QUEUED", "OK"), ev("EXTRACT", "FAILED", error="no text")], None)
    assert failed["status"] == "FAILED" and failed["error"] == "no text"


def test_content_duplicate_wins():
    r = derive_status([ev("QUEUED", "OK"), ev("TRANSLATE", "OK"), ev("DEDUPE", "DUPLICATE", existing_invoice_id="abc")], None)
    assert r["status"] == "DUPLICATE" and r["existing_invoice_id"] == "abc"
