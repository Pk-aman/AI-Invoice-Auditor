"""Read API over the two-write lifecycle: review pending vs. finalized, immutable AI report, duplicates, feedback gating."""
import json

import psycopg
import pytest

from tests.conftest import SERVICES_URL, add_invoice, pytestmark_db

pytestmark = pytestmark_db
REPORT = {"invoice_id": "x", "validation_run": 1, "recommendation": "review", "summary": "Price differs.", "reasons": ["price"],
          "invoice": {"invoice_number": "INV-9", "vendor_name": "Global Logistics Ltd", "currency": "USD", "total_amount": "960.00"},
          "data_findings": [], "discrepancies": [{"field_name": "unit_price", "invoice_value": "14", "erp_value": "12",
                                                  "deviation_pct": "16.6", "severity": "medium"}]}


def _doc(client, iid):
    items = client.get("/api/v1/documents").json()["items"]
    return next(d for d in items if d["invoice_id"] == iid)


def test_pending_invoice_is_awaiting_review_and_enriched_from_the_report_file(client, db):
    iid = add_invoice(db, report=REPORT)
    doc = _doc(client, iid)
    assert doc["processing_status"] == "review" and doc["audit_result"] == "not_available" and doc["status"] == "INGESTED"
    assert doc["invoice_number"] == "INV-9" and doc["vendor_name"] == "Global Logistics Ltd" and doc["recommendation"] == "review"
    stats = client.get("/api/v1/documents/stats").json()
    assert stats["awaiting_review"] == 1 and stats["audited"] == 0
    detail = client.get(f"/api/v1/documents/{doc['document_id']}").json()
    assert detail["findings"] and detail["summary"] == "Price differs."
    inv = client.get(f"/api/v1/invoices/{iid}").json()
    assert inv["status"] == "INGESTED" and inv["report_available"] is True and inv["ai_report_available"] is False
    assert inv["invoice_number"] is None  # nothing but ingestion evidence is in PostgreSQL yet


@pytest.mark.parametrize("final,result", [("APPROVED", "approved"), ("REJECTED", "rejected"), ("SKIPPED_MAX_RETRIES", "needs_attention")])
def test_finalized_statuses_map_to_audit_results(client, db, final, result):
    iid = add_invoice(db, final=final, report=REPORT)
    doc = _doc(client, iid)
    assert doc["processing_status"] == "audited" and doc["audit_result"] == result and doc["status"] == final


def test_ai_report_endpoint_serves_the_immutable_snapshot(client, db):
    pending = add_invoice(db, report=REPORT)
    r = client.get(f"/api/v1/invoices/{pending}/ai-report")
    assert r.status_code == 404 and r.json()["error"]["code"] == "ai_report_not_stored"
    iid = add_invoice(db, final="APPROVED", report=REPORT)
    r = client.get(f"/api/v1/invoices/{iid}/ai-report")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json") and r.json() == REPORT
    assert client.get(f"/api/v1/invoices/{iid}").json()["ai_report_available"] is True
    assert client.get("/api/v1/invoices/00000000-0000-0000-0000-000000000000/ai-report").status_code == 404


def test_feedback_is_gated_until_finalization_and_never_touches_the_ai_report(client, db):
    pending = add_invoice(db, report=REPORT)
    r = client.post(f"/api/v1/invoices/{pending}/feedback", json={"field_name": "po_number", "corrected_value": "PO-1", "corrected_by": "jane"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invoice_under_review"
    iid = add_invoice(db, final="APPROVED", report=REPORT)
    before = db.execute("SELECT ai_audit_report FROM audit.invoice_audit WHERE invoice_id=%s", (iid,)).fetchone()["ai_audit_report"]
    r = client.post(f"/api/v1/invoices/{iid}/feedback", json={"field_name": "po_number", "corrected_value": "PO-1", "corrected_by": "jane"})
    assert r.status_code == 201 and r.json()["corrected_value"] == "PO-1"
    after = db.execute("SELECT ai_audit_report FROM audit.invoice_audit WHERE invoice_id=%s", (iid,)).fetchone()["ai_audit_report"]
    assert before == after and json.loads(client.get(f"/api/v1/invoices/{iid}/ai-report").text) == REPORT
    assert client.get(f"/api/v1/invoices/{iid}/feedback").json()[0]["corrected_value"] == "PO-1"


def test_services_role_cannot_modify_invoice_rows_at_all():
    with psycopg.connect(SERVICES_URL) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("UPDATE audit.invoice_audit SET ai_audit_report='{}'")


def test_review_history_endpoint(client, db):
    iid = add_invoice(db, final="APPROVED", report=REPORT)
    db.execute("INSERT INTO audit.review_decisions (invoice_id, review_round, decision, reviewer, comments, human_check_count)"
               " VALUES (%s,1,'approve','jane','ok',1)", (iid,))
    (d,) = client.get(f"/api/v1/invoices/{iid}/reviews").json()
    assert (d["decision"], d["reviewer"], d["comments"], d["human_check_count"]) == ("approve", "jane", "ok", 1)


def test_content_duplicate_upload_is_reported_as_duplicate_not_processing_forever(client, db):
    original = add_invoice(db, final="APPROVED", report=REPORT)
    chk = "d" * 64
    db.execute("INSERT INTO audit.processing_events (file_path, file_checksum, stage, state, detail) VALUES ('/x', %s, 'QUEUED','OK', %s)",
               (chk, json.dumps({"original_filename": "copy.pdf"})))
    db.execute("INSERT INTO audit.processing_events (invoice_id, file_path, file_checksum, stage, state, detail) VALUES (%s,'/x',%s,"
               "'DEDUPE','DUPLICATE', %s)", (original, chk, json.dumps({"kind": "content", "existing_invoice_id": original})))
    s = client.get(f"/api/v1/uploads/{chk}/status").json()
    assert s["status"] == "DUPLICATE" and s["existing_invoice_id"] == original
    dup = next(d for d in client.get("/api/v1/documents").json()["items"] if d["checksum"] == chk)
    assert dup["processing_status"] == "duplicate" and str(dup["duplicate_of"]) == original


def test_upload_status_reports_needs_review(client, db):
    iid = add_invoice(db, report=REPORT)
    chk = db.execute("SELECT file_checksum FROM audit.invoice_audit WHERE invoice_id=%s", (iid,)).fetchone()["file_checksum"]
    s = client.get(f"/api/v1/uploads/{chk}/status").json()
    assert s["status"] == "NEEDS_REVIEW" and s["invoice_id"] == iid


def test_report_download_is_invoice_scoped_and_served_with_security_headers(client, db):
    iid = add_invoice(db, final="APPROVED", report=REPORT)
    r = client.get(f"/api/v1/invoices/{iid}/report?format=html")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "sandbox" and r.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/v1/invoices/{iid}/report?format=json").json() == REPORT
    assert client.get("/api/v1/invoices/..%2F..%2Fetc%2Fpasswd/report").status_code in (404, 422)
    assert client.get(f"/api/v1/invoices/{iid}/report?format=../../etc/passwd").status_code == 422
