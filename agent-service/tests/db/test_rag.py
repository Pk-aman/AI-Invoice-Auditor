"""RAG response contract, Redis caching and RAGAS state handling (LLM/RAGAS stubbed)."""
import time

import pytest

from app.agents.indexing import index_invoice
from app.core.rag_cache import cache_key
from app.services.ragas_evaluator import RagasScores
from app.services.rag_service import RagService
from tests.db.helpers import req, start

pytestmark = pytest.mark.db


class StubEvaluator:
    def __init__(self, scores=RagasScores(0.94, 0.98, 0.96), delay=0.0, fail=False):
        self.scores, self.delay, self.fail, self.calls = scores, delay, fail, 0

    def evaluate(self, question, answer, contexts):
        self.calls += 1
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("judge unavailable")
        return self.scores


def _wait_done(svc, eval_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        ev = svc.evaluation(eval_id)
        if ev["state"] != "pending":
            return ev
        time.sleep(0.02)
    raise AssertionError("evaluation did not finish")


@pytest.fixture
def indexed(make_ctx, tmp_path, settings):
    settings.rag_reflection = "on"
    ctx, pipe, review, iid = start(make_ctx, tmp_path)
    index_invoice(ctx.qdrant, settings.qdrant_collection, ctx.llm, invoice_id=iid, invoice_number="INV-1001", vendor="Global Logistics Ltd",
                  language="en", text="Invoice INV-1001 was flagged because the unit price differs.", report=pipe.invoice_graph.get_state(pipe.config(iid)).values["report"])
    return ctx, review, iid


def test_response_contract_pending_then_done_and_cache_hit(indexed):
    ctx, _, iid = indexed
    ev = StubEvaluator(delay=0.2)
    svc = RagService(ctx, ev)
    q = "Why was this invoice flagged?"
    first = svc.query(q, iid)
    assert first["invoice_id"] == iid and first["cache_hit"] is False and first["route"] == "open"
    assert first["answer"] and first["sources"] and {"source_type", "chunk_text"} <= set(first["sources"][0])
    assert {s["source_type"] for s in first["sources"]} <= {"ai_report", "invoice_text"}
    m = first["ragas_metrics"]  # not computed yet: must NOT pretend otherwise
    assert m["state"] == "pending" and m["context_relevance"] is None and m["faithfulness"] is None and m["answer_relevance"] is None
    assert m["low_confidence_warning"] is None and first["eval_id"]
    done = _wait_done(svc, first["eval_id"])
    assert done["state"] == "done" and (done["context_relevance"], done["faithfulness"], done["answer_relevance"]) == (0.94, 0.98, 0.96)
    assert done["low_confidence_warning"] is False
    # same question (different case/whitespace) -> cache hit, same eval, scores now filled in
    second = svc.query("  why was THIS invoice   flagged?  ", iid)
    assert second["cache_hit"] is True and second["answer"] == first["answer"] and second["eval_id"] == first["eval_id"]
    assert second["ragas_metrics"]["state"] == "done" and second["ragas_metrics"]["faithfulness"] == 0.98
    assert ev.calls == 1, "a cache hit must not re-run RAGAS"
    assert 3590 <= ctx.redis.ttl(cache_key(iid, q)) <= 3600


def test_low_confidence_warning_and_failed_evaluation(indexed):
    ctx, _, iid = indexed
    svc = RagService(ctx, StubEvaluator(RagasScores(0.4, 0.9, 0.9)))
    r = svc.query("Why was this invoice flagged?", iid)
    assert _wait_done(svc, r["eval_id"])["low_confidence_warning"] is True
    bad = RagService(ctx, StubEvaluator(fail=True))
    r2 = bad.query("Explain the discrepancies of this invoice", iid)
    ev = _wait_done(bad, r2["eval_id"])
    assert ev["state"] == "failed" and "judge unavailable" in ev["error"] and ev["faithfulness"] is None


def test_cache_is_scoped_per_invoice_and_cleared_on_correction(indexed, make_ctx, tmp_path):
    ctx, review, iid = indexed
    svc = RagService(ctx, StubEvaluator())
    q = "Why was this invoice flagged?"
    svc.query(q, iid)
    svc.query(q, None)
    assert svc.query(q, iid)["cache_hit"] is True and svc.query(q, None)["cache_hit"] is True
    assert ctx.redis.exists(cache_key(iid, q)) and ctx.redis.exists(cache_key(None, q)) and cache_key(iid, q) != cache_key(None, q)
    review.review(iid, req("reject_and_revalidate", po_number="PO-1001", comments="fix"))
    assert not ctx.redis.exists(cache_key(iid, q)) and not ctx.redis.exists(cache_key(None, q))
    assert svc.query(q, iid)["cache_hit"] is False  # fresh retrieval after the correction


def test_structured_questions_are_not_cached_and_have_no_ragas(indexed):
    ctx, _, iid = indexed
    svc = RagService(ctx, StubEvaluator())
    r = svc.query("How many invoices are there in total?")
    assert r["route"] == "structured" and r["cache_hit"] is False and r["ragas_metrics"]["state"] == "not_applicable"
    assert r["sources"][0]["source_type"] == "database" and r["sources"][0]["chunk_text"]
    assert svc.query("How many invoices are there in total?")["cache_hit"] is False
    assert not list(ctx.redis.scan_iter("rag:*"))


def test_reindex_rebuilds_from_postgres_and_reports_only(indexed, settings):
    ctx, _, iid = indexed
    svc = RagService(ctx, StubEvaluator())
    before = ctx.qdrant.count(settings.qdrant_collection).count
    n = svc.reindex(iid)
    after = ctx.qdrant.count(settings.qdrant_collection).count
    assert n == after and after > 0 and before >= after  # old/stale chunks were deleted, not duplicated
