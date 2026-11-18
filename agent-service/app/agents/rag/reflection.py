"""Reflection agent: RAG Triad (answer relevance, groundedness, context relevance) scored by an LLM judge.

Lightweight in-house implementation of the RAGAS metrics (the `ragas` package pulls in heavy dependencies
that would break the image-size budget); same three definitions, same 0..1 scale.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app import prompts
from app.core.llm import LLM, extract_json

log = logging.getLogger(__name__)


@dataclass
class TriadScores:
    relevance: float
    groundedness: float
    context_relevance: float


def _score(llm: LLM, system: str, user: str) -> float:
    data = extract_json(llm.complete("reasoning", system, user, json_mode=True, max_tokens=200))
    return max(0.0, min(1.0, float(data["score"])))


def reflect(question: str, answer: str, context_text: str, llm: LLM) -> TriadScores:
    cfg = prompts.load("rag")["judge"]
    system = cfg["system"]
    return TriadScores(
        relevance=_score(llm, system, prompts.render(cfg["answer_relevance"], question=question, answer=answer)),
        groundedness=_score(llm, system, prompts.render(cfg["groundedness"], context=context_text, answer=answer)),
        context_relevance=_score(llm, system, prompts.render(cfg["context_relevance"], question=question, context=context_text)),
    )
