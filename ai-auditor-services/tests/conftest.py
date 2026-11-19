"""API tests run against a REAL PostgreSQL initialised from infra/postgres/init, as the real service role (services_rw).

    TEST_ADMIN_DATABASE_URL=postgresql://postgres:<pw>@localhost:55432/auditor    (seeds/truncates)
    TEST_SERVICES_DATABASE_URL=postgresql://services_rw:<pw>@localhost:55432/auditor
Without them the tests are skipped.
"""
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
SERVICES_URL = os.environ.get("TEST_SERVICES_DATABASE_URL")
_TMP = Path(tempfile.mkdtemp(prefix="svc-tests-"))
os.environ.setdefault("DATABASE_URL", SERVICES_URL or "postgresql://x:y@localhost/none")
os.environ["INCOMING_DIR"] = str(_TMP / "incoming")
os.environ["REPORTS_DIR"] = str(_TMP / "reports")

pytestmark_db = pytest.mark.skipif(not (ADMIN_URL and SERVICES_URL), reason="test database not configured")


@pytest.fixture(scope="session")
def admin():
    if not (ADMIN_URL and SERVICES_URL):
        pytest.skip("test database not configured")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(ADMIN_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture
def db(admin):
    admin.execute("TRUNCATE audit.human_feedback, audit.review_decisions, audit.validation_reports, audit.discrepancies,"
                  " audit.invoice_line_items, audit.processing_events, audit.rag_evaluations, audit.invoice_audit CASCADE")
    return admin


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    Path(os.environ["INCOMING_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(os.environ["REPORTS_DIR"]).mkdir(parents=True, exist_ok=True)
    with TestClient(create_app()) as c:
        yield c


def add_invoice(db, *, final: str | None = None, file_checksum: str | None = None, with_events: bool = True, report: dict | None = None,
                invoice_id: str | None = None) -> str:
    """Insert an invoice as the agent would: Write #1 (INGESTED) and optionally Write #2 (final: APPROVED|REJECTED|...)."""
    iid = invoice_id or str(uuid.uuid4())
    chk = file_checksum or uuid.uuid4().hex + uuid.uuid4().hex
    db.execute("INSERT INTO audit.invoice_audit (invoice_id, file_path, file_checksum, content_checksum, extraction_method,"
               " raw_extracted_text, translated_text, source_language, translation_confidence, thread_id)"
               " VALUES (%s,%s,%s,%s,'docx','raw','translated','en',0.99,%s)",
               (iid, f"/data/incoming/{chk[:12]}_inv.docx", chk, uuid.uuid4().hex + uuid.uuid4().hex, iid))
    if with_events:
        for stage, state in (("QUEUED", "OK"), ("DETECTED", "OK"), ("INGEST", "OK")):
            db.execute("INSERT INTO audit.processing_events (invoice_id, file_path, file_checksum, stage, state, detail)"
                       " VALUES (%s,%s,%s,%s,%s,%s)", (iid, f"/data/incoming/{chk[:12]}_inv.docx", chk, stage, state,
                                                       json.dumps({"original_filename": "inv.docx"})))
        db.execute("INSERT INTO audit.processing_events (invoice_id, file_checksum, stage, state) VALUES (%s,%s,'HUMAN_REVIEW','PENDING')",
                   (iid, chk))
    if report is not None:
        (Path(os.environ["REPORTS_DIR"])).mkdir(parents=True, exist_ok=True)
        (Path(os.environ["REPORTS_DIR"]) / f"{iid}.json").write_text(json.dumps(report))
        (Path(os.environ["REPORTS_DIR"]) / f"{iid}.html").write_text("<html><body>report</body></html>")
    if final:
        db.execute("INSERT INTO audit.validation_reports (invoice_id, validation_run, report) VALUES (%s,1,%s)", (iid, json.dumps(report or {})))
        db.execute("UPDATE audit.invoice_audit SET invoice_number='INV-1', vendor_name='Acme', currency='USD', total_amount=10,"
                   " validation_status=%s, recommendation='approve', ai_audit_report=%s, status=%s, human_check_count=1,"
                   " finalized_at=now() WHERE invoice_id=%s", ("skipped_max_retries" if final == "SKIPPED_MAX_RETRIES" else "passed",
                                                 json.dumps(report or {"run": 1}, sort_keys=True), final, iid))
        db.execute("INSERT INTO audit.processing_events (invoice_id, file_checksum, stage, state) VALUES (%s,%s,'COMPLETED','OK')", (iid, chk))
    return iid
