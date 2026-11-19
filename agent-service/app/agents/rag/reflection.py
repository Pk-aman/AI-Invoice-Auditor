# ================================================================
# AI INVOICE AUDITOR - RAG: REFLECTION AGENT (RAGAS) + RETRY
# ================================================================

import logging
import math

# RAGAS
from ragas import EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.run_config import RunConfig

from ragas.metrics import (
    ContextRelevance,
    Faithfulness,
    ResponseRelevancy
)

from app.agents.rag import setup
from app.graphs.state import RAGState

log = logging.getLogger(__name__)

# ================================================================
# STEP 16: REFLECTION AGENT USING RAGAS
# ================================================================

# RAG Triad:
#   context_relevance - are the chunks given to the LLM relevant to the question?
#   faithfulness      - is every claim in the answer supported by those chunks? (groundedness)
#   answer_relevance  - does the answer address the question?

def reflection_node(state: RAGState):

    # RAG_REFLECTION=off skips the evaluation.

    if not setup.settings.reflection_enabled:

        return {
            "scores": {},
            "passed": True,
            "evaluation_error": ""
        }

    question = state["question"]

    answer = state["answer"]

    # Evaluate the chunks the answer was generated from.

    contexts = state["context_chunks"]

    if not contexts:

        return {
            "scores": {},
            "passed": False,
            "evaluation_error": "No retrieved contexts"
        }

    try:

        # STEP 1: Prepare evaluation dataset.

        dataset = EvaluationDataset.from_list([

            {
                "user_input": question,
                "retrieved_contexts": contexts,
                "response": answer
            }

        ])

        # STEP 2: Initialize evaluator models.

        evaluator_llm = LangchainLLMWrapper(setup.llm)

        evaluator_embeddings = LangchainEmbeddingsWrapper(
            setup.embeddings
        )

        # STEP 3: Evaluate RAG Triad.

        result = evaluate(

            dataset=dataset,

            metrics=[
                ContextRelevance(),
                Faithfulness(),
                ResponseRelevancy()
            ],

            llm=evaluator_llm,

            embeddings=evaluator_embeddings,

            run_config=RunConfig(timeout=setup.settings.ragas_timeout_s),

            show_progress=False
        )

        # STEP 4: Extract scores.

        scores = result.to_pandas().iloc[0].to_dict()

        # Metric names in RAGAS 0.4.x.

        context_relevance = float(
            scores.get(
                "nv_context_relevance",
                math.nan
            )
        )

        faithfulness = float(
            scores.get(
                "faithfulness",
                math.nan
            )
        )

        answer_relevance = float(
            scores.get(
                "answer_relevancy",
                math.nan
            )
        )

        final_scores = {
            "context_relevance": context_relevance,
            "faithfulness": faithfulness,
            "answer_relevance": answer_relevance
        }

        # RAGAS returns NaN when the judge LLM's output could not be parsed.
        # That is an evaluation failure, not a low score.

        failed = [name for name, value in final_scores.items() if math.isnan(value)]

        if failed:
            raise ValueError(f"RAGAS could not score: {', '.join(failed)}")

        # STEP 5: Check thresholds (RAGAS_MIN_* in agent-service/.env, default 0.7).

        passed = (
            context_relevance >= setup.settings.ragas_min_context_relevance
            and faithfulness >= setup.settings.ragas_min_faithfulness
            and answer_relevance >= setup.settings.ragas_min_answer_relevance
        )

        return {
            "scores": final_scores,
            "passed": passed,
            "evaluation_error": ""
        }

    except Exception as error:

        log.warning("RAGAS evaluation failed: %s", error)

        # Stop retrying if evaluation itself failed.

        return {
            "scores": {},
            "passed": False,
            "evaluation_error": f"{type(error).__name__}: {error}"
        }

# ================================================================
# STEP 17: CONDITIONAL ROUTING AFTER REFLECTION
# ================================================================

def route_after_reflection(state: RAGState):

    # If evaluation failed, do not retry.

    if state["evaluation_error"]:

        return "end"

    # If all metrics passed, finish.

    if state["passed"]:

        return "end"

    # Otherwise allow a limited retry.

    if state["retry_count"] < setup.MAX_RETRIES:

        return "retry"

    return "end"

# ================================================================
# STEP 18: RETRY NODE
# ================================================================

def retry_node(state: RAGState):

    log.info("Evaluation failed. Retrying retrieval...")

    return {
        "retry_count": state["retry_count"] + 1
    }
