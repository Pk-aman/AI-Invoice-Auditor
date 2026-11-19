"""RAG response contract, Redis caching, RAGAS reflection + limited retry (LLM, embeddings and RAGAS faked)."""
import pytest

from app.agents.indexing import index_invoice
from app.agents.rag.router import AWAITING_REVIEW_NOTE
from app.core.rag_cache import cache_key
from app.services.rag_service import RagService
from tests.db.helpers import req, start
from tests.rag_fakes import GOOD, LOW, FakeChat, FakeEvaluate, use_fakes

pytestmark = pytest.mark.db

ROUTES = {"how many invoices": {"query_type": "sql", "sql_action": "invoice_count"},
          "total amount": {"query_type": "sql", "sql_action": "total_amount"}}


@pytest.fixture
def indexed(make_ctx, tmp_path, settings):
    settings.rag_reflection = "on"
    ctx, pipe, review, iid = start(make_ctx, tmp_path)  # the invoice is waiting for its human review
    index_invoice(ctx.qdrant, settings.qdrant_collection, ctx.llm, invoice_id=iid, invoice_number="INV-1001", vendor="Global Logistics Ltd",
                  language="en", text="Invoice INV-1001 was flagged because the unit price differs.", report=pipe.invoice_graph.get_state(pipe.config(iid)).values["report"])
    return ctx, review, iid


@pytest.fixture
def service(indexed, monkeypatch):
    def make(evaluate=None, chat=None):
        svc = RagService(indexed[0])
        ev = evaluate or FakeEvaluate(GOOD)
        use_fakes(monkeypatch, chat or FakeChat(ROUTES), ev)
        return svc, ev
    return make


def test_response_contract_scores_come_with_the_answer_and_cache_hit(indexed, service):
    ctx, _, iid = indexed
    svc, ev = service()
    q = "Why was this invoice flagged?"
    first = svc.query(q, iid)
    assert first["invoice_id"] == iid and first["cache_hit"] is False and first["route"] == "open" and first["retry_count"] == 0
    assert first["answer"] and first["sources"] and {"source_type", "chunk_text"} <= set(first["sources"][0])
    assert {s["source_type"] for s in first["sources"]} <= {"ai_report", "invoice_text"}
    m = first["ragas_metrics"]  # RAGAS runs in the graph: computed scores, stored in audit.rag_evaluations
    assert m["state"] == "done" and (m["context_relevance"], m["faithfulness"], m["answer_relevance"]) == GOOD
    assert m["low_confidence_warning"] is False and first["eval_id"]
    assert svc.evaluation(first["eval_id"])["faithfulness"] == 0.98
    # same question (different case/whitespace) -> cache hit, same eval, RAGAS not re-run
    second = svc.query("  why was THIS invoice   flagged?  ", iid)
    assert second["cache_hit"] is True and second["answer"] == first["answer"] and second["eval_id"] == first["eval_id"]
    assert second["ragas_metrics"]["state"] == "done" and second["ragas_metrics"]["faithfulness"] == 0.98
    assert ev.calls == 1, "a cache hit must not re-run RAGAS"
    assert 3590 <= ctx.redis.ttl(cache_key(iid, q)) <= 3600


def test_low_scores_retry_once_then_warn_and_failed_evaluation(indexed, service):
    _, _, iid = indexed
    svc, ev = service(FakeEvaluate(LOW))
    r = svc.query("Why was this invoice flagged?", iid)
    assert ev.calls == 2 and r["retry_count"] == 1  # one limited retry (RAG_MAX_RETRIES=1)
    assert r["ragas_metrics"]["state"] == "done" and r["ragas_metrics"]["low_confidence_warning"] is True
    bad, bad_ev = service(FakeEvaluate(RuntimeError("judge unavailable")))
    m = bad.query("Explain the discrepancies of this invoice", iid)["ragas_metrics"]
    assert bad_ev.calls == 1 and m["state"] == "failed" and "judge unavailable" in m["error"] and m["faithfulness"] is None


def test_cache_is_scoped_per_invoice_and_cleared_on_correction(indexed, service):
    ctx, review, iid = indexed
    svc, _ = service()
    q = "Why was this invoice flagged?"
    svc.query(q, iid)
    svc.query(q, None)
    assert svc.query(q, iid)["cache_hit"] is True and svc.query(q, None)["cache_hit"] is True
    assert ctx.redis.exists(cache_key(iid, q)) and ctx.redis.exists(cache_key(None, q)) and cache_key(iid, q) != cache_key(None, q)
    review.review(iid, req("reject_and_revalidate", po_number="PO-1001", comments="fix"))
    assert not ctx.redis.exists(cache_key(iid, q)) and not ctx.redis.exists(cache_key(None, q))
    assert svc.query(q, iid)["cache_hit"] is False  # fresh retrieval after the correction


def test_structured_questions_use_sql_are_not_cached_and_have_no_ragas(indexed, service):
    ctx, _, _ = indexed
    svc, ev = service()
    r = svc.query("How many invoices are there in total?")
    assert r["route"] == "structured" and r["cache_hit"] is False and r["ragas_metrics"]["state"] == "not_applicable"
    assert r["sources"][0]["source_type"] == "database" and '"invoices": 1' in r["answer"]
    assert svc.query("How many invoices are there in total?")["cache_hit"] is False
    assert not list(ctx.redis.scan_iter("rag:*")) and ev.calls == 0


def test_invoice_question_awaiting_review_uses_the_report_and_is_not_cached(indexed, service):
    ctx, _, iid = indexed
    svc, _ = service()
    r = svc.query("What is the total amount of this invoice?", iid)
    assert r["route"] == "open" and r["answer"].startswith(AWAITING_REVIEW_NOTE) and r["ragas_metrics"]["state"] == "done"
    assert not list(ctx.redis.scan_iter("rag:*"))


def test_finalized_invoice_question_is_answered_from_postgres(indexed, service):
    _, review, iid = indexed
    review.review(iid, req("approve"))
    svc, _ = service()
    r = svc.query("What is the total amount of this invoice?", iid)
    assert r["route"] == "structured" and '"total_amount": 960.0' in r["answer"] and '"currency": "USD"' in r["answer"]


def test_reindex_rebuilds_from_postgres_and_reports_only(indexed, service, settings):
    ctx, _, iid = indexed
    svc, _ = service()
    before = ctx.qdrant.count(settings.qdrant_collection).count
    n = svc.reindex(iid)
    after = ctx.qdrant.count(settings.qdrant_collection).count
    assert n == after and after > 0 and before >= after  # old/stale chunks were deleted, not duplicated
