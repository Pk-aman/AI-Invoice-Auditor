"""Human-in-the-loop review workflow: approve / reject / reject_and_revalidate, the 3-check ceiling, concurrency."""
import json
import threading
from decimal import Decimal

import psycopg
import pytest
from pydantic import ValidationError

from app.agents.review import ReviewError
from app.api.schemas import ReviewRequest
from tests.db.helpers import price_drift_invoice, req, start

pytestmark = pytest.mark.db


def _row(admin_query):
    return admin_query("SELECT * FROM audit.invoice_audit")[0]


def test_initial_audit_pauses_for_review_then_approve_finalizes(make_ctx, tmp_path, admin_query):
    _, pipe, svc, iid = start(make_ctx, tmp_path)
    assert pipe.pending_nodes(iid) == ("human_review",)
    state = svc.state(iid)
    assert state.awaiting_review and state.status == "NEEDS_REPORT_REVIEW" and state.remaining_checks == 4
    assert state.invoice["invoice_number"] == "INV-1001" and len(state.invoice["line_items"]) == 2
    res = svc.review(iid, req("approve", comments="looks fine"))
    assert (res.status, res.human_check_count, res.invoice_id) == ("APPROVED", 1, iid)
    row = _row(admin_query)
    assert row["status"] == "APPROVED" and row["finalized_at"] is not None and row["recommendation"] == res.recommendation
    dec = admin_query("SELECT decision, reviewer, comments, human_check_count FROM audit.review_decisions")
    assert [(d["decision"], d["reviewer"], d["comments"], d["human_check_count"]) for d in dec] == [("approve", "auditor_jane", "looks fine", 1)]
    assert svc.state(iid).status == "APPROVED" and not svc.state(iid).awaiting_review


def test_reject_and_revalidate_inserts_feedback_reruns_validation_and_pauses_again(make_ctx, tmp_path, admin_query):
    ctx, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    first = svc.state(iid)
    assert first.validation_run == 1 and any(d["field_name"].endswith("unit_price") for d in first.report["discrepancies"])
    res = svc.review(iid, req("reject_and_revalidate", comments="price typo", line__1__unit_price="12.00"))
    assert res.status == "NEEDS_REPORT_REVIEW" and res.human_check_count == 1 and res.validation_run == 2
    assert res.discrepancies_count == len(svc.state(iid).report["discrepancies"])
    # feedback is an INSERT that keeps the original value; the row is still not finalized
    (fb,) = admin_query("SELECT * FROM audit.human_feedback")
    assert (fb["field_name"], fb["corrected_value"], fb["corrected_by"], fb["review_round"]) == ("line:1:unit_price", "12.0000", "auditor_jane", 1)
    assert Decimal(fb["original_value"]) == Decimal("14") and fb["revalidated"] is True and fb["corrected_at"] is not None
    row = _row(admin_query)
    assert row["status"] == "INGESTED" and row["human_check_count"] == 1 and row["invoice_number"] is None
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_line_items")[0]["n"] == 0  # still no Write #2
    state = svc.state(iid)
    assert state.awaiting_review and state.validation_run == 2 and state.remaining_checks == 3
    assert state.invoice["line_items"][0]["unit_price"] == "12.0000" and state.corrections == {"line:1:unit_price": "12.0000"}
    assert not any(d["field_name"].endswith("unit_price") for d in state.report["discrepancies"])  # the fix resolved it


def test_ai_report_stays_immutable_across_revalidation(make_ctx, tmp_path, admin_query, settings):
    _, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    original = json.loads((settings.reports_dir / f"{iid}.run1.json").read_text())
    svc.review(iid, req("reject_and_revalidate", line__1__unit_price="12.00"))
    svc.review(iid, req("approve"))
    row = _row(admin_query)
    stored = json.loads(row["ai_audit_report"])
    assert stored == original and stored["validation_run"] == 1 and stored["corrections"] == {}
    runs = {r["validation_run"]: r["report"] for r in admin_query("SELECT validation_run, report FROM audit.validation_reports")}
    assert set(runs) == {1, 2} and runs[1] == original and runs[2]["corrections"] == {"line:1:unit_price": "12.0000"}
    assert row["validation_run"] == 2
    disc = admin_query("SELECT validation_run, superseded FROM audit.discrepancies")
    assert disc and all(d["superseded"] == (d["validation_run"] == 1) for d in disc)
    assert json.loads((settings.reports_dir / f"{iid}.run1.json").read_text()) == original  # file untouched too
    state = svc.state(iid)
    assert state.ai_audit_report == original and state.report["validation_run"] == 2


def test_fourth_human_check_finalizes_skipped_max_retries(make_ctx, tmp_path, admin_query):
    _, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    for n in (1, 2, 3):
        res = svc.review(iid, req("reject_and_revalidate", line__1__unit_price=f"{13 + n}.00"))
        assert (res.status, res.human_check_count) == ("NEEDS_REPORT_REVIEW", n)
    res = svc.review(iid, req("reject_and_revalidate", comments="still wrong", line__1__unit_price="20.00"))
    assert res.status == "SKIPPED_MAX_RETRIES" and res.validation_status == "skipped_max_retries" and res.human_check_count == 4
    row = _row(admin_query)
    assert (row["status"], row["validation_status"], row["human_check_count"]) == ("SKIPPED_MAX_RETRIES", "skipped_max_retries", 4)
    assert row["finalized_at"] is not None and row["ai_audit_report"]
    assert admin_query("SELECT count(*) AS n FROM audit.human_feedback")[0]["n"] == 4
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_line_items")[0]["n"] == 2
    with pytest.raises(ReviewError) as exc:  # a fifth review can never happen: no review is requested again
        svc.review(iid, req("approve"))
    assert exc.value.status == 409


def test_reject_finalizes_as_rejected_and_keeps_notes_and_corrections(make_ctx, tmp_path, admin_query):
    _, _, svc, iid = start(make_ctx, tmp_path)
    res = svc.review(iid, req("reject", comments="fraudulent", po_number="PO-9999"))
    assert (res.status, res.recommendation, res.human_check_count) == ("REJECTED", "reject", 1)
    row = _row(admin_query)
    assert row["status"] == "REJECTED" and row["recommendation"] == "reject" and row["po_number"] != "PO-9999"  # not applied
    assert admin_query("SELECT corrected_value FROM audit.human_feedback")[0]["corrected_value"] == "PO-9999"
    assert admin_query("SELECT comments FROM audit.review_decisions")[0]["comments"] == "fraudulent"


def test_rag_cache_is_invalidated_before_revalidation(make_ctx, tmp_path):
    ctx, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    r = ctx.redis
    for k in (f"rag:{iid}:aaa", f"rag:{iid}:bbb", "rag:all:ccc", "rag:other-invoice:ddd", "llm:eee"):
        r.set(k, "x")
    svc.review(iid, req("reject_and_revalidate", line__1__unit_price="12.00"))
    keys = {k.decode() for k in r.scan_iter("*")}
    assert keys == {"rag:other-invoice:ddd", "llm:eee"}  # this invoice + cross-invoice answers gone; others + LLM cache kept


def test_cache_invalidation_failure_is_surfaced_not_hidden(make_ctx, tmp_path):
    ctx, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())

    class Broken:
        def scan_iter(self, *a, **k):
            raise ConnectionError("redis down")

    ctx.redis = Broken()  # the graph's RagCache was built from the original client: swap it in place
    from app.graphs import invoice_graph  # noqa: F401  (nodes close over ctx.redis at build time)
    svc.pipeline.invoice_graph = __import__("app.graphs.invoice_graph", fromlist=["x"]).build_invoice_graph(ctx, ctx.checkpointer)
    res = svc.review(iid, req("reject_and_revalidate", line__1__unit_price="12.00"))
    assert res.status == "NEEDS_REPORT_REVIEW" and res.warnings and "could not be cleared" in res.warnings[0]


@pytest.mark.parametrize("kwargs,status,code", [
    ({"decision": "approve", "extra": {"po_number": "PO-1"}}, 422, "corrections_not_allowed"),
    ({"decision": "reject_and_revalidate"}, 422, "corrections_required"),
    ({"decision": "reject_and_revalidate", "extra": {"nonsense_field": "x"}}, 422, "unsupported_field"),
    ({"decision": "reject_and_revalidate", "extra": {"line__9__unit_price": "1"}}, 422, "line_not_found"),
    ({"decision": "reject_and_revalidate", "extra": {"line__1__unit_price": "abc"}}, 422, "invalid_value"),
    ({"decision": "reject_and_revalidate", "extra": {"invoice_date": "14/03/2025"}}, 422, "invalid_value"),
    ({"decision": "reject_and_revalidate", "extra": {"currency": "DOLLARS"}}, 422, "invalid_value"),
    ({"decision": "reject_and_revalidate", "extra": {"total_amount": "-5"}}, 422, "invalid_value"),
])
def test_invalid_reviews_are_rejected_with_4xx_and_write_nothing(make_ctx, tmp_path, admin_query, kwargs, status, code):
    _, _, svc, iid = start(make_ctx, tmp_path)
    with pytest.raises(ReviewError) as exc:
        svc.review(iid, req(kwargs["decision"], **kwargs.get("extra", {})))
    assert (exc.value.status, exc.value.code) == (status, code)
    assert admin_query("SELECT count(*) AS n FROM audit.human_feedback")[0]["n"] == 0
    assert _row(admin_query)["human_check_count"] == 0 and svc.state(iid).awaiting_review


def test_unknown_invoice_not_yet_reviewable_and_unauthorised_reviewer(make_ctx, tmp_path, settings):
    ctx, _, svc, iid = start(make_ctx, tmp_path)
    with pytest.raises(ReviewError) as e1:
        svc.review("00000000-0000-0000-0000-000000000000", req("approve"))
    assert e1.value.status == 404
    settings.reviewers_allowed = "auditor_jane"
    with pytest.raises(ReviewError) as e2:
        svc.review(iid, req("approve", reviewer="mallory"))
    assert e2.value.status == 403
    svc.review(iid, req("approve", reviewer="Auditor_Jane"))  # allow-list is case-insensitive


def test_request_model_rejects_bad_decisions_and_reviewers():
    for bad in ({"decision": "maybe", "reviewer": "a"}, {"decision": "approve", "reviewer": ""}, {"decision": "approve", "reviewer": "   "},
                {"decision": "approve", "reviewer": "<script>"}, {"decision": "approve"}):
        with pytest.raises(ValidationError):
            ReviewRequest(**bad)
    assert ReviewRequest(decision="approve", reviewer="  auditor_jane ").reviewer == "auditor_jane"


def test_concurrent_reviews_are_serialised(make_ctx, tmp_path, admin_query):
    _, _, svc, iid = start(make_ctx, tmp_path, parsed=price_drift_invoice())
    out: list = []

    def submit(value):
        try:
            out.append(svc.review(iid, req("reject_and_revalidate", reviewer=f"r{value}", line__1__unit_price=value)))
        except ReviewError as exc:
            out.append(exc)

    threads = [threading.Thread(target=submit, args=(v,)) for v in ("12.00", "13.00", "11.00")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert all(not isinstance(o, Exception) for o in out), out
    assert sorted(o.human_check_count for o in out) == [1, 2, 3]  # strictly serialised: each saw the previous result
    rounds = [r["review_round"] for r in admin_query("SELECT review_round FROM audit.human_feedback ORDER BY review_round")]
    assert rounds == [1, 2, 3] and _row(admin_query)["human_check_count"] == 3


def test_concurrent_approvals_only_one_finalizes(make_ctx, tmp_path, admin_query):
    _, _, svc, iid = start(make_ctx, tmp_path)
    out: list = []

    def submit():
        try:
            out.append(svc.review(iid, req("approve")))
        except ReviewError as exc:
            out.append(exc)

    threads = [threading.Thread(target=submit) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    ok = [o for o in out if not isinstance(o, Exception)]
    assert len(ok) == 1 and all(isinstance(o, ReviewError) and o.status == 409 for o in out if o not in ok)
    assert admin_query("SELECT count(*) AS n FROM audit.review_decisions")[0]["n"] == 1
    assert admin_query("SELECT count(*) AS n FROM audit.invoice_line_items")[0]["n"] == 2


def test_database_enforces_the_ceiling_and_finalized_rows_are_frozen(make_ctx, tmp_path, pg_pools):
    ctx, _, _, iid = start(make_ctx, tmp_path)
    pool, _, _ = pg_pools
    with pytest.raises(psycopg.errors.CheckViolation), pool.connection() as c:
        c.execute("UPDATE audit.invoice_audit SET human_check_count=5 WHERE invoice_id=%s", (iid,))
