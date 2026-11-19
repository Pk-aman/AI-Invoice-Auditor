"""Assistant page: RAG response contract (cache hit badge, sources, RAGAS scores shown only when computed)."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import api_client

PAGE = Path(__file__).resolve().parents[1] / "app" / "pages" / "assistant.py"
ANSWER = {"invoice_id": None, "answer": "Because the unit price differs [S1].", "cache_hit": False, "route": "open", "eval_id": "e1",
          "sources": [{"source_type": "ai_report", "chunk_text": "Discrepancy on unit_price", "invoice_number": "INV-1001"}],
          "ragas_metrics": {"state": "pending", "context_relevance": None, "faithfulness": None, "answer_relevance": None,
                            "low_confidence_warning": None}}


def _ask(monkeypatch, reply, evaluation=None):
    monkeypatch.setattr(api_client, "list_documents", lambda _p: {"items": []})
    monkeypatch.setattr(api_client, "ask", lambda *a, **k: reply)
    monkeypatch.setattr(api_client, "evaluation", lambda _id: evaluation or {"state": "pending"})
    at = AppTest.from_file(str(PAGE), default_timeout=20).run()
    at.chat_input[0].set_value("Why was this invoice flagged?").run()
    return at


def test_pending_scores_are_not_claimed_before_they_exist(monkeypatch):
    at = _ask(monkeypatch, ANSWER)
    assert not at.exception
    assert any("unit price differs" in m.value for m in at.markdown)
    assert any("Checking answer quality" in c.value for c in at.caption)
    assert not [m for m in at.metric if m.label.startswith("Faithfulness")]


def test_scores_low_confidence_warning_and_cache_badge(monkeypatch):
    reply = {**ANSWER, "cache_hit": True, "ragas_metrics": {"state": "done", "context_relevance": 0.94, "faithfulness": 0.42,
                                                              "answer_relevance": 0.96, "low_confidence_warning": True}}
    at = _ask(monkeypatch, reply)
    assert {m.label: m.value for m in at.metric} == {"Context relevance": "94%", "Faithfulness (grounded in the documents)": "42%",
                                                      "Answer relevance": "96%"}
    assert any("Low confidence" in w.value for w in at.warning)
    assert any("saved response" in c.value for c in at.caption)


def test_structured_answers_show_no_quality_scores(monkeypatch):
    reply = {**ANSWER, "route": "structured", "eval_id": None, "ragas_metrics": {"state": "not_applicable"},
             "sources": [{"source_type": "database", "chunk_text": "3 invoices"}]}
    at = _ask(monkeypatch, reply)
    assert not at.metric and not at.exception
