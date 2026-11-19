"""Test doubles for the RAG module (app/agents/rag): no LLM, embedding or RAGAS call ever leaves the test."""
from __future__ import annotations

import math
import re

import pandas as pd
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.agents.rag import reflection, setup
from tests.conftest import FakeLLM


class FakeChat(Runnable):
    """Stands in for ChatLiteLLM: plain calls return `answer`; with_structured_output returns router/rerank objects.

    routes:  {"substring of the question": {"query_type": ..., "sql_action": ..., "invoice_number": ...}}
    rerank:  scores to return, "fail" to raise, or None for "first passage best" (descending scores)
    """

    def __init__(self, routes=None, rerank=None, answer="The invoice was flagged because the unit price differs from the PO."):
        self.routes, self.rerank, self.answer, self.calls = routes or {}, rerank, answer, []

    def invoke(self, input, config=None, **kwargs):
        self.calls.append("generate")
        return AIMessage(content=self.answer)

    def with_structured_output(self, schema, **kwargs):
        return RunnableLambda(lambda prompt_value: self._structured(schema, prompt_value.to_string()))

    def _structured(self, schema, text):
        if schema.__name__ == "QueryRoute":
            self.calls.append("router")
            question = text.rsplit("Human:", 1)[-1]  # only the user's question, not the router instructions
            for key, route in self.routes.items():
                if key.lower() in question.lower():
                    return schema(**route)
            return schema(query_type="rag")
        if schema.__name__ == "RerankScores":
            self.calls.append("rerank")
            if self.rerank == "fail":
                raise ValueError("unparseable rerank output")
            n = int(re.search(r"Passages \((\d+) in total\)", text).group(1))
            return schema(scores=self.rerank if self.rerank is not None else [float(n - i) for i in range(n)])
        raise AssertionError(f"unexpected schema {schema}")


class FakeEmbeddings:
    """Same vectors as FakeLLM.embed, which the indexing agent uses in tests."""

    def embed_query(self, text):
        return FakeLLM().embed([text])[0]

    def embed_documents(self, texts):
        return FakeLLM().embed(texts)


class FakeEvaluate:
    """Replaces ragas.evaluate: returns the given (context_relevance, faithfulness, answer_relevance) one call after another."""

    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def __call__(self, dataset=None, metrics=None, llm=None, embeddings=None, **kwargs):
        r = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(r, Exception):
            raise r
        row = {"nv_context_relevance": r[0], "faithfulness": r[1], "answer_relevancy": r[2]}
        return type("Result", (), {"to_pandas": lambda self: pd.DataFrame([row])})()


GOOD = (0.94, 0.98, 0.96)
LOW = (0.4, 0.9, 0.9)
NAN = (math.nan, 0.9, 0.9)


def use_fakes(monkeypatch, chat: FakeChat, evaluate: FakeEvaluate | None = None):
    """Call after setup.init(ctx) (RagService does that)."""
    monkeypatch.setattr(setup, "llm", chat)
    monkeypatch.setattr(setup, "embeddings", FakeEmbeddings())
    monkeypatch.setattr(reflection, "evaluate", evaluate or FakeEvaluate(GOOD))
    monkeypatch.setattr(reflection, "LangchainLLMWrapper", lambda llm: llm)
    monkeypatch.setattr(reflection, "LangchainEmbeddingsWrapper", lambda emb: emb)
