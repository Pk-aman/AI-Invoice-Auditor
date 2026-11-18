"""Typed wrapper over agent-service (re-validate + RAG)."""
from __future__ import annotations

import httpx

from app.api.http import cached_client, parse, send
from app.config import get_settings
from app.models import PipelineStatus, RagAnswer, RagEvaluation, RevalidateResponse


class AgentClient:
    def __init__(self, client: httpx.Client):
        self._c = client

    def revalidate(self, invoice_id: str) -> RevalidateResponse:
        return parse(RevalidateResponse,
                     send(self._c, "POST", f"/invoices/{invoice_id}/revalidate").json())

    def status(self, invoice_id: str) -> PipelineStatus:
        return parse(PipelineStatus, send(self._c, "GET", f"/invoices/{invoice_id}/status").json())

    def rag_query(self, question: str, invoice_id: str | None = None, vendor: str | None = None,
                  session_id: str | None = None) -> RagAnswer:
        body = {"question": question, "invoice_id": invoice_id, "vendor": vendor,
                "session_id": session_id}
        return parse(RagAnswer, send(self._c, "POST", "/rag/query", json=body).json())

    def rag_evaluation(self, eval_id: str) -> RagEvaluation:
        return parse(RagEvaluation, send(self._c, "GET", f"/rag/evaluations/{eval_id}").json())


def get_agent() -> AgentClient:
    s = get_settings()
    return AgentClient(cached_client(s.agent_url, s.agent_timeout_s))
