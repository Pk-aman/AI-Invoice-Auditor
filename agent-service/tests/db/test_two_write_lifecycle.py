"""The strict two-write PostgreSQL lifecycle: SQL Write #1 (ingestion) and SQL Write #2 (finalization)."""
import json
import threading
import psycopg
import pytest

from app.persistence.audit_repo import canonical_json
from app.schemas.lifecycle import Finalization
from app.schemas.models import Discrepancy, ExtractedInvoice
from app.services.pipeline import Pipeline
from tests.conftest import GOOD_INVOICE, FakeErp, FakeLLM, make_docx
from tests.db.helpers import price_drift_invoice, req, start

pytestmark = pytest.mark.db


def test_before_review_only_ingestion_evidence_is_written(make_ctx, tmp_path, admin_query, settings):
    ctx, _, _, iid = start(make_ctx, tmp_path)
    (row,) = admin_query("SELECT * FROM audit.invoice_audit")
    assert row["status"] == "INGESTED" and row["finalized_at"] is None and row["human_check_count"] == 0
    # SQL Write #1 content
    assert row["file_checksum"] and row["content_checksum"] and row["extraction_method"] == "docx"
    assert "Global Logistics" in row["raw_extracted_text"] and row["translated_text"] and row["source_language"] == "en"
    assert row["thread_id"] == iid and row["translation_confidence"] is not None
    # nothing from parsing / validation / reporting is persisted yet
    for col in ("invoice_number", "vendor_name", "po_number", "invoice_date", "currency", "total_amount", "validation_status",
                "recommendation", "ai_audit_report", "report_path", "extracted_raw"):
        assert row[col] is None, col
    for table in ("invoice_line_items", "discrepancies", "validation_reports", "human_feedback", "review_decisions"):
        assert admin_query(f"SELECT count(*) AS n FROM audit.{table}")[0]["n"] == 0, table  # noqa: S608
    # ...while the in-flight work exists as report FILES, vectors and the checkpoint
    assert (settings.reports_dir / f"{iid}.json").is_file() and (settings.reports_dir / f"{iid}.run1.json").is_file()
    assert ctx.qdrant.count(settings.qdrant_collection).count > 0


def test_exactly_two_business_writes_and_finalization_is_one_transaction(make_ctx, tmp_path, write_log, admin_query):
    _, _, svc, iid = start(make_ctx, tmp_path)
    log = write_log()
    assert [(t, o) for t, o, _ in log] == [("invoice_audit", "INSERT")]  # Write #1: one INSERT, nothing else
    svc.review(iid, req("approve"))
    final = write_log()[1:]
    ops = [(t, o) for t, o, _ in final]
    assert ops.count(("invoice_audit", "UPDATE")) == 1 and ("invoice_audit", "INSERT") not in ops
    assert {"invoice_audit", "invoice_line_items", "validation_reports"} <= {t for t, _, _ in final} <= {
        "invoice_audit", "invoice_line_items", "validation_reports", "discrepancies"}
    assert len({tx for _, _, tx in final}) == 1, "finalization must be ONE transaction"
    (row,) = admin_query("SELECT * FROM audit.invoice_audit")
    assert row["status"] == "APPROVED" and row["invoice_number"] == "INV-1001" and row["finalized_at"] is not None
    assert row["validation_status"] in ("passed", "partial", "failed") and row["recommendation"]
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_line_items")[0]["n"] == 2
    assert row["human_check_count"] == 1


def test_finalization_rolls_back_completely_on_error(make_ctx, tmp_path, admin_query):
    ctx, _, _, iid = start(make_ctx, tmp_path)
    fin = Finalization(
        status="APPROVED", validation_status="passed", recommendation="approve", validation_run=1, human_check_count=1,
        invoice=ExtractedInvoice.model_validate({"invoice_number": "X", "line_items": [{"line_number": 1, "item_code": "A"}]}),
        ai_audit_report={"run": 1}, run_reports={1: {"run": 1}},
        discrepancy_runs={1: [Discrepancy.model_construct(field_name="qty", severity="x" * 40, code="mismatch")]})  # too long -> DB error
    with pytest.raises(psycopg.errors.DataError):
        ctx.repo.finalize_invoice(iid, fin)
    (row,) = admin_query("SELECT * FROM audit.invoice_audit")
    assert row["status"] == "INGESTED" and row["finalized_at"] is None and row["invoice_number"] is None
    assert row["ai_audit_report"] is None and row["human_check_count"] == 0
    for table in ("invoice_line_items", "discrepancies", "validation_reports", "review_decisions", "human_feedback"):
        assert admin_query(f"SELECT count(*) AS n FROM audit.{table}")[0]["n"] == 0, table  # noqa: S608


def test_finalize_is_idempotent_no_duplicate_rows(make_ctx, tmp_path, admin_query):
    ctx, pipe, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    svc.review(iid, req("reject_and_revalidate", comments="fix price", line__1__unit_price="12.00"))
    snapshot = pipe.invoice_graph.get_state(pipe.config(iid)).values
    svc.review(iid, req("approve"))
    counts = {t: admin_query(f"SELECT count(*) AS n FROM audit.{t}")[0]["n"] for t in  # noqa: S608
              ("invoice_line_items", "discrepancies", "validation_reports", "human_feedback", "review_decisions")}
    # replaying SQL Write #2 (e.g. a retried node) must not duplicate anything
    from app.schemas.lifecycle import DecisionItem, FeedbackItem

    fin = Finalization(
        status="APPROVED", validation_status="passed", recommendation="approve", validation_run=2, human_check_count=2,
        invoice=ExtractedInvoice.model_validate(snapshot["invoice"]), ai_audit_report=snapshot["original_report"],
        run_reports={1: snapshot["original_report"], 2: snapshot["report"]},
        discrepancy_runs={1: [Discrepancy(field_name="unit_price")], 2: []},
        feedback=[FeedbackItem(field_name="line:1:unit_price", corrected_value="12.00", corrected_by="auditor_jane", review_round=1)],
        decisions=[DecisionItem(review_round=2, decision="approve", reviewer="auditor_jane", human_check_count=2)])
    assert ctx.repo.finalize_invoice(iid, fin) is False
    after = {t: admin_query(f"SELECT count(*) AS n FROM audit.{t}")[0]["n"] for t in counts}  # noqa: S608
    assert after == counts


def test_concurrent_finalize_only_one_wins(make_ctx, tmp_path, admin_query):
    ctx, pipe, _, iid = start(make_ctx, tmp_path)
    snap = pipe.invoice_graph.get_state(pipe.config(iid)).values
    fin = Finalization(status="APPROVED", validation_status="passed", recommendation="approve", validation_run=1, human_check_count=1,
                       invoice=ExtractedInvoice.model_validate(snap["invoice"]), ai_audit_report=snap["original_report"],
                       run_reports={1: snap["original_report"]}, discrepancy_runs={1: []})
    results: list = []
    threads = [threading.Thread(target=lambda: results.append(ctx.repo.finalize_invoice(iid, fin))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [False, False, False, True]
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_line_items")[0]["n"] == 2


def test_post_ingest_failure_is_resumable_and_does_not_duplicate(make_ctx, tmp_path, admin_query):
    erp = FakeErp(down=True)
    ctx = make_ctx(llm=FakeLLM(GOOD_INVOICE), erp=erp)
    pipe = Pipeline(ctx)
    with pytest.raises(Exception, match="erp down"):
        pipe.process_path(make_docx(tmp_path / "a.docx"))
    (row,) = admin_query("SELECT status FROM audit.invoice_audit")
    assert row["status"] == "INGESTED"
    iid = admin_query("SELECT invoice_id FROM audit.invoice_audit")[0]["invoice_id"]
    assert pipe.pending_nodes(str(iid)) == ("business_validate",)
    events = [(e["stage"], e["state"]) for e in admin_query("SELECT stage, state FROM audit.processing_events ORDER BY id")]
    assert ("BUSINESS_VALIDATION", "FAILED") in events
    erp.down = False
    out = pipe.retry_sync(str(iid))
    assert out["status"] == "NEEDS_REPORT_REVIEW" and pipe.pending_nodes(str(iid)) == ("human_review",)
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_audit")[0]["n"] == 1


def test_ai_audit_report_is_immutable_in_the_database(make_ctx, tmp_path, admin_query, pg_pools):
    ctx, _, svc, iid = start(make_ctx, tmp_path)
    svc.review(iid, req("approve"))
    (row,) = admin_query("SELECT ai_audit_report FROM audit.invoice_audit")
    stored = json.loads(row["ai_audit_report"])
    assert stored["validation_run"] == 1 and stored["invoice_id"] == iid
    assert row["ai_audit_report"] == canonical_json(stored)
    pool, _, admin = pg_pools
    for p in (pool, admin):  # neither the app role nor the admin may rewrite it
        with pytest.raises(psycopg.errors.Error), p.connection() as c:
            c.execute("UPDATE audit.invoice_audit SET ai_audit_report='{}' WHERE invoice_id=%s", (iid,))
