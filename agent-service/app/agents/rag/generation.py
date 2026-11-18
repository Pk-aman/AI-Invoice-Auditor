"""Generation agent: grounded answer from the assembled context (Redis-cached through the LLM wrapper)."""
from __future__ import annotations

from app import prompts
from app.core.llm import LLM

NO_ANSWER = "I don't know based on the processed invoices."


def generate(question: str, context_text: str, llm: LLM) -> str:
    if not context_text.strip():
        return NO_ANSWER
    cfg = prompts.load("rag")["generation"]
    return llm.complete("generation", prompts.persona() + "\n\n" + cfg["system"],
                        prompts.render(cfg["user"], context=context_text, question=question), max_tokens=800).strip()
