"""The RAGAS adapter: pinned package API shape, score validation, low-confidence thresholds."""
import importlib.metadata
import math

import pytest

from app.core.config import Settings
from app.services.ragas_evaluator import RagasEvaluationError, RagasEvaluator, RagasScores, _value


def test_pinned_ragas_api_shape_is_available():
    assert importlib.metadata.version("ragas") == "0.4.4", "bump the pin AND re-verify the adapter together"
    from ragas.embeddings import LiteLLMEmbeddings  # noqa: F401
    from ragas.llms import llm_factory  # noqa: F401
    from ragas.metrics import MetricResult  # noqa: F401
    from ragas.metrics.collections import AnswerRelevancy, ContextRelevance, Faithfulness

    import inspect

    assert "retrieved_contexts" in inspect.signature(ContextRelevance.ascore).parameters
    assert {"response", "retrieved_contexts"} <= set(inspect.signature(Faithfulness.ascore).parameters)
    assert set(inspect.signature(AnswerRelevancy.ascore).parameters) >= {"user_input", "response"}


class _Metric:
    def __init__(self, value):
        self.value, self.seen = value, None

    async def ascore(self, **kwargs):
        from ragas.metrics import MetricResult

        self.seen = kwargs
        return MetricResult(value=self.value)


def _evaluator(ctx_v, faith_v, rel_v):
    ev = RagasEvaluator(Settings())
    ev._metrics = {"context_relevance": _Metric(ctx_v), "faithfulness": _Metric(faith_v), "answer_relevance": _Metric(rel_v)}
    return ev


def test_scores_are_mapped_to_the_three_required_metrics():
    ev = _evaluator(0.94, 0.98, 0.96)
    scores = ev.evaluate("why flagged?", "because", ["chunk one", "chunk two"])
    assert scores == RagasScores(0.94, 0.98, 0.96)
    assert ev._metrics["context_relevance"].seen == {"user_input": "why flagged?", "retrieved_contexts": ["chunk one", "chunk two"]}
    assert ev._metrics["faithfulness"].seen["response"] == "because"
    assert ev._metrics["answer_relevance"].seen == {"user_input": "why flagged?", "response": "because"}


@pytest.mark.parametrize("scores,low", [((0.9, 0.9, 0.9), False), ((0.69, 0.9, 0.9), True), ((0.9, 0.5, 0.9), True), ((0.9, 0.9, 0.1), True)])
def test_low_confidence_warning_uses_configured_thresholds(scores, low):
    assert RagasScores(*scores).low_confidence(Settings()) is low
    strict = Settings(ragas_min_context_relevance=0.95, ragas_min_faithfulness=0.95, ragas_min_answer_relevance=0.95)
    assert RagasScores(0.9, 0.9, 0.9).low_confidence(strict) is True


def test_nan_and_garbage_scores_are_errors_never_zero():
    with pytest.raises(RagasEvaluationError, match="NaN"):
        _value(math.nan, "faithfulness")
    with pytest.raises(RagasEvaluationError, match="non-numeric"):
        _value(None, "faithfulness")
    assert _value(1.7, "x") == 1.0 and _value(-0.2, "x") == 0.0
    with pytest.raises(RagasEvaluationError):
        _evaluator(0.9, 0.9, 0.9).evaluate("q", "a", [])


def test_judge_uses_markdown_json_mode_by_default_and_rejects_unknown_modes():
    import instructor

    assert Settings().ragas_instructor_mode == "MD_JSON" and hasattr(instructor.Mode, "MD_JSON")
    ev = RagasEvaluator(Settings(ragas_instructor_mode="NOPE"))
    with pytest.raises(RagasEvaluationError, match="RAGAS_INSTRUCTOR_MODE"):
        ev._build()


def test_real_ragas_metrics_can_be_constructed_offline():
    """Builds the LiteLLM-backed judge + the three ragas metrics without any network call."""
    ev = RagasEvaluator(Settings(llm_reasoning_model="ollama/llama3.2", embedding_model="ollama/nomic-embed-text"))
    assert set(ev.metrics) == {"context_relevance", "faithfulness", "answer_relevance"}
