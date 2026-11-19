"""RAGAS evaluation (context relevance, faithfulness, answer relevance) using the `ragas` package.

This is the ONLY module that touches ragas. It is a thin adapter because ragas' public API changes between minor
versions: the dependency is pinned (`ragas==0.4.4` in requirements.txt) and `tests/unit/test_ragas_adapter.py`
fails loudly if the pinned API shape drifts.

  metric (ragas.metrics.collections)   -> reported as
  ContextRelevance                     -> context_relevance   (retrieved context vs. question)
  Faithfulness                         -> faithfulness        (answer claims supported by the context = groundedness)
  AnswerRelevancy                      -> answer_relevance    (answer vs. question; uses embeddings)

The judge LLM / embeddings go through LiteLLM (same Bedrock/Ollama configuration as the rest of the agent), so no
extra credentials are introduced. Scores are 0..1; a missing/NaN score is an error, never silently 0.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

from app.core.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RagasScores:
    context_relevance: float
    faithfulness: float
    answer_relevance: float

    def low_confidence(self, s: Settings) -> bool:
        return (self.context_relevance < s.ragas_min_context_relevance
                or self.faithfulness < s.ragas_min_faithfulness
                or self.answer_relevance < s.ragas_min_answer_relevance)


class RagasEvaluationError(RuntimeError):
    pass


def _value(result, name: str) -> float:
    raw = getattr(result, "value", result)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RagasEvaluationError(f"ragas metric {name} returned a non-numeric score: {raw!r}") from None
    if math.isnan(value):
        raise RagasEvaluationError(f"ragas metric {name} returned NaN (judge output could not be parsed)")
    return max(0.0, min(1.0, value))


class RagasEvaluator:
    def __init__(self, settings: Settings):
        self.s = settings
        self._metrics: dict | None = None

    def _extra(self, model: str) -> dict:
        return {"api_base": self.s.ollama_base_url} if model.startswith("ollama") else {}

    def _build(self) -> dict:
        import instructor
        import litellm
        from ragas.embeddings import LiteLLMEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, ContextRelevance, Faithfulness

        try:
            mode = getattr(instructor.Mode, self.s.ragas_instructor_mode.upper())
        except AttributeError:
            raise RagasEvaluationError(f"unknown RAGAS_INSTRUCTOR_MODE {self.s.ragas_instructor_mode!r}") from None
        judge = self.s.llm_reasoning_model
        client = instructor.from_litellm(litellm.acompletion, mode=mode)
        llm = llm_factory(judge, provider="litellm", client=client, adapter="litellm",
                          temperature=self.s.llm_temperature, timeout=self.s.ragas_timeout_s, **self._extra(judge))
        embeddings = LiteLLMEmbeddings(model=self.s.embedding_model, timeout=self.s.ragas_timeout_s, **self._extra(self.s.embedding_model))
        return {
            "context_relevance": ContextRelevance(llm=llm),
            "faithfulness": Faithfulness(llm=llm),
            "answer_relevance": AnswerRelevancy(llm=llm, embeddings=embeddings),
        }

    @property
    def metrics(self) -> dict:
        if self._metrics is None:
            self._metrics = self._build()
        return self._metrics

    async def _ascore(self, question: str, answer: str, contexts: list[str]) -> RagasScores:
        m = self.metrics
        ctx, faith, rel = await asyncio.gather(
            m["context_relevance"].ascore(user_input=question, retrieved_contexts=contexts),
            m["faithfulness"].ascore(user_input=question, response=answer, retrieved_contexts=contexts),
            m["answer_relevance"].ascore(user_input=question, response=answer),
        )
        return RagasScores(_value(ctx, "context_relevance"), _value(faith, "faithfulness"), _value(rel, "answer_relevance"))

    def evaluate(self, question: str, answer: str, contexts: list[str]) -> RagasScores:
        """Blocking; run it in a worker thread (it owns its own event loop)."""
        if not contexts:
            raise RagasEvaluationError("no retrieved contexts to evaluate")
        return asyncio.run(self._ascore(question, answer, contexts))
