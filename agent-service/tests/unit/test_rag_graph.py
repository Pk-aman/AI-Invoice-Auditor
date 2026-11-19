"""RAG graph nodes without external services: router + SQL agent, LLM reranker, RAGAS reflection and limited retry."""
from __future__ import annotations

from types import SimpleNamespace

import fakeredis
import pytest
from langchain_core.documents import Document
from qdrant_client import QdrantClient

from app.agents.indexing import index_invoice
from app.agents.rag import augmentation, router, setup
from app.clients.qdrant_client import ensure_collection
from app.core.config import Settings
from app.graphs import rag_graph
from app.services import rag_service
from tests.conftest import EMBED_DIM, FakeLLM
from tests.rag_fakes import GOOD, LOW, NAN, FakeChat, FakeEvaluate, use_fakes

IID = "11111111-1111-1111-1111-111111111111"
REPORT = {"invoice": {"invoice_number": "INV-1001", "vendor_name": "Global Logistics Ltd"}, "recommendation": "review",
          "validation_status": "failed", "summary": "Unit price above the PO.", "reasons": ["unit price differs from the PO"],
          "discrepancies": [{"field_name": "unit_price", "severity": "high", "invoice_value": 14.4, "erp_value": 12.0, "deviation_pct": 20}]}

ROUTES = {
    "total amount": {"query_type": "sql", "sql_action": "total_amount"},
    "need attention": {"query_type": "sql", "sql_action": "invoices_needing_attention"},
    "discrepancies for INV-1001": {"query_type": "sql", "sql_action": "discrepancies", "invoice_number": "INV-1001"},
}


@pytest.fixture
def rag(monkeypatch):
    """setup.init with an in-memory Qdrant holding one indexed invoice; returns a function that runs the graph."""

    def make(chat=None, evaluate=None, status="APPROVED", **settings):
        s = Settings(**{"embedding_dim": EMBED_DIM, "rag_reflection": "on", **settings})
        q = QdrantClient(":memory:")
        ensure_collection(q, s.qdrant_collection, EMBED_DIM)
        index_invoice(q, s.qdrant_collection, FakeLLM(), invoice_id=IID, invoice_number="INV-1001", vendor="Global Logistics Ltd",
                      language="en", text="Invoice INV-1001 for pallet film and gloves, total 960 USD.", report=REPORT)
        setup.init(SimpleNamespace(settings=s, qdrant=q, redis=fakeredis.FakeRedis(), pool=None))
        chat = chat or FakeChat(ROUTES)
        ev = evaluate or FakeEvaluate(GOOD)
        use_fakes(monkeypatch, chat, ev)
        db = []

        def run_query(sql, params=()):
            db.append((sql, params))
            if "SELECT status" in sql:
                return [] if status is None else [{"status": status}]
            if "WHERE invoice_number" in sql:
                return [{"invoice_id": IID}]
            if "total_amount" in sql and params:
                return [{"invoice_id": IID, "invoice_number": "INV-1001", "vendor_name": "Global Logistics Ltd", "total_amount": 960.0,
                         "currency": "USD"}]
            return [{"invoice_number": "INV-1001", "recommendation": "review"}]

        monkeypatch.setattr(router, "run_query", run_query)
        monkeypatch.setattr(rag_service, "rag_graph", rag_graph.build_graph())

        def ask(question, invoice_id=IID):
            return rag_service.ask_invoice_question(question, invoice_id)

        return SimpleNamespace(ask=ask, chat=chat, evaluate=ev, db=db)

    return make


# ---- router + SQL agent -------------------------------------------------------------------------------------
def test_structured_invoice_question_is_answered_with_sql(rag):
    r = rag()
    out = r.ask("What is the total amount of this invoice?")
    assert out["query_type"] == "sql" and '"total_amount": 960.0' in out["answer"] and out["sources"][0]["source_type"] == "database"
    assert ("SELECT status FROM audit.invoice_audit WHERE invoice_id = %s", (IID,)) in r.db
    assert out["scores"] == {} and r.evaluate.calls == 0  # no RAGAS for SQL answers


def test_question_about_all_invoices_needs_no_invoice(rag):
    r = rag()
    out = r.ask("Which invoices need attention?", invoice_id=None)
    assert out["query_type"] == "sql" and "INV-1001" in out["answer"]
    assert "recommendation IN ('review', 'reject')" in r.db[-1][0]


def test_invoice_named_in_the_question_is_looked_up(rag):
    r = rag()
    out = r.ask("Show the discrepancies for INV-1001", invoice_id=None)
    assert out["query_type"] == "sql" and r.db[0][1] == ("INV-1001",) and r.db[-1][1] == (IID,)
    assert "superseded = FALSE" in r.db[-1][0]


def test_invoice_question_without_an_invoice_asks_for_one(rag):
    r = rag(FakeChat({"total amount": {"query_type": "sql", "sql_action": "total_amount"}}))
    assert r.ask("What is the total amount?", invoice_id=None)["answer"].startswith("Please provide an invoice ID")


def test_invoice_awaiting_review_is_answered_from_the_report(rag):
    r = rag(status="INGESTED")
    out = r.ask("What is the total amount of this invoice?")
    assert out["query_type"] == "rag" and out["note"] == router.AWAITING_REVIEW_NOTE and out["context_chunks"]
    assert r.evaluate.calls == 1


def test_unknown_invoice(rag):
    assert rag(status=None).ask("What is the total amount of this invoice?")["answer"] == "That invoice was not found."


def test_router_failure_falls_back_to_rag(rag, monkeypatch):
    r = rag()
    monkeypatch.setattr(r.chat, "_structured", lambda schema, text: (_ for _ in ()).throw(ValueError("bad json")))
    assert router.router_node({"question": "anything"})["query_type"] == "rag"


# ---- reranker (augmentation) --------------------------------------------------------------------------------
def _docs():
    return [Document(page_content=t, metadata={"score": s, "source_type": "ai_report"}) for t, s in (("alpha", 0.9), ("beta", 0.8), ("gamma", 0.7))]


def test_llm_rerank_orders_by_llm_score(rag):
    rag(FakeChat(rerank=[1, 9, 5]))
    ranked = augmentation.rerank_documents("q", _docs())
    assert [d.page_content for d, _ in ranked] == ["beta", "gamma", "alpha"] and ranked[0][1] == 9


@pytest.mark.parametrize("bad", ["fail", [1, 2]])
def test_rerank_falls_back_to_vector_order(rag, bad):
    rag(FakeChat(rerank=bad))
    assert [d.page_content for d, _ in augmentation.rerank_documents("q", _docs())] == ["alpha", "beta", "gamma"]


def test_augmentation_keeps_final_context_k_chunks(rag):
    rag(FakeChat(rerank=[1, 9, 5]), rag_final_context_k=2)
    out = augmentation.augmentation_node({"question": "q", "retrieved_docs": _docs()})
    assert out["context_chunks"] == ["beta", "gamma"] and [s["rerank_score"] for s in out["sources"]] == [9, 5]


# ---- reflection (RAGAS) + limited retry ---------------------------------------------------------------------
def test_open_question_good_scores_no_retry(rag):
    r = rag()
    out = r.ask("Why was this invoice flagged?")
    assert out["query_type"] == "rag" and out["retry_count"] == 0 and out["evaluation_passed"] is True
    assert out["scores"] == {"context_relevance": 0.94, "faithfulness": 0.98, "answer_relevance": 0.96}
    assert "rerank" in r.chat.calls and {s["source_type"] for s in out["sources"]} <= {"ai_report", "invoice_text"}


def test_low_scores_retry_at_most_max_retries(rag):
    r = rag(evaluate=FakeEvaluate(LOW))
    out = r.ask("Why was this invoice flagged?")
    assert r.evaluate.calls == 2 and out["retry_count"] == 1 and out["evaluation_passed"] is False


def test_retry_that_passes_stops(rag):
    r = rag(evaluate=FakeEvaluate(LOW, GOOD), rag_max_retries=3)
    out = r.ask("Why was this invoice flagged?")
    assert r.evaluate.calls == 2 and out["evaluation_passed"] is True


@pytest.mark.parametrize("result", [RuntimeError("judge unavailable"), NAN])
def test_ragas_failure_is_not_retried(rag, result):
    r = rag(evaluate=FakeEvaluate(result))
    out = r.ask("Why was this invoice flagged?")
    assert r.evaluate.calls == 1 and out["retry_count"] == 0 and out["evaluation_error"] and out["scores"] == {}


def test_reflection_can_be_switched_off(rag):
    r = rag(rag_reflection="off")
    out = r.ask("Why was this invoice flagged?")
    assert r.evaluate.calls == 0 and out["answer"] and out["evaluation_error"] == ""
