"""RAG facade: answer a question (Redis-cached), then asynchronously score it with RAGAS.

Public response (see api/schemas.py::RagAnswer):
  {invoice_id, answer, cache_hit, ragas_metrics{context_relevance, faithfulness, answer_relevance,
   low_confidence_warning, state}, sources[{source_type, chunk_text, ...}], route, eval_id}

RAGAS needs several LLM calls, so it never blocks the answer: `ragas_metrics.state` is "pending" (all scores null)
until the evaluation finishes; poll GET /rag/evaluations/{eval_id}. A cached answer returns the evaluation's
CURRENT state/scores, never a claim that was not computed.
"""
from __future__ import annotations

import logging
import threading

from app.core.context import AppContext
from app.core.observability import record_score, traced_span
from app.core.rag_cache import RagCache
from app.graphs.rag_graph import build_rag_graph
from app.services.ragas_evaluator import RagasEvaluator

log = logging.getLogger(__name__)


class RagService:
    def __init__(self, ctx: AppContext, evaluator: RagasEvaluator | None = None):
        self.ctx = ctx
        self.graph = build_rag_graph(ctx)
        self.cache = RagCache(ctx.redis, ctx.settings.rag_cache_ttl_s)
        self.evaluator = evaluator or RagasEvaluator(ctx.settings)

    # ---- query ----------------------------------------------------------------------------------
    def query(self, question: str, invoice_id: str | None = None, vendor: str | None = None) -> dict:
        cached = self.cache.get(invoice_id, question, vendor)
        if cached is not None:
            return self._response(invoice_id, cached, cache_hit=True)

        out = self.graph.invoke({"question": question, "invoice_id": invoice_id, "vendor": vendor})
        answer, route, sources = out.get("answer", ""), out.get("route", "open"), out.get("sources", [])
        payload = {"answer": answer, "route": route, "sources": sources, "eval_id": None}

        chunks = out.get("context_chunks") or []
        if route == "open" and chunks:  # SQL answers change with every new invoice: never cached
            if self.ctx.settings.reflection_enabled:
                eval_id = self.ctx.repo.create_rag_eval(question, answer, route, invoice_id)
                payload["eval_id"] = eval_id
                threading.Thread(target=self._reflect, args=(eval_id, question, answer, chunks), daemon=True,
                                 name="rag-reflect").start()
            self.cache.set(invoice_id, question, payload, vendor)
        return self._response(invoice_id, payload, cache_hit=False)

    def _response(self, invoice_id: str | None, payload: dict, *, cache_hit: bool) -> dict:
        eval_id = payload.get("eval_id")
        if eval_id:
            metrics = self.evaluation(eval_id) or {"state": "pending"}
        elif payload.get("route") == "structured":
            metrics = {"state": "not_applicable"}
        else:
            metrics = {"state": "disabled"} if not self.ctx.settings.reflection_enabled else {"state": "not_applicable"}
        keys = ("context_relevance", "faithfulness", "answer_relevance", "low_confidence_warning", "state", "error")
        return {"invoice_id": invoice_id, "answer": payload["answer"], "cache_hit": cache_hit,
                "ragas_metrics": {k: metrics.get(k) for k in keys if k in metrics}, "sources": payload.get("sources", []),
                "route": payload.get("route", "open"), "eval_id": eval_id}

    # ---- RAGAS (background) --------------------------------------------------------------------------
    def _reflect(self, eval_id: str, question: str, answer: str, chunks: list[str]) -> None:
        try:
            with traced_span("rag_reflection", eval_id=eval_id):
                scores = self.evaluator.evaluate(question, answer, chunks)
                low = scores.low_confidence(self.ctx.settings)
                self.ctx.repo.save_rag_scores(eval_id, context_relevance=scores.context_relevance, faithfulness=scores.faithfulness,
                                              answer_relevance=scores.answer_relevance, low_confidence=low)
                for name in ("context_relevance", "faithfulness", "answer_relevance"):
                    record_score(f"ragas_{name}", getattr(scores, name))
        except Exception as exc:  # noqa: BLE001 - never lose the reason; the answer itself was already returned
            log.exception("RAGAS evaluation failed", extra={"eval_id": eval_id})
            try:
                self.ctx.repo.fail_rag_eval(eval_id, f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                log.exception("could not record the RAGAS failure", extra={"eval_id": eval_id})

    def evaluation(self, eval_id: str) -> dict | None:
        row = self.ctx.repo.get_rag_eval(eval_id)
        if row is None:
            return None
        base = {"eval_id": eval_id, "state": row["state"], "error": row["error"], "context_relevance": None, "faithfulness": None,
                "answer_relevance": None, "low_confidence_warning": None}
        if row["state"] == "done":
            base.update(context_relevance=float(row["context_relevance"]), faithfulness=float(row["faithfulness"]),
                        answer_relevance=float(row["answer_relevance"]), low_confidence_warning=bool(row["low_confidence"]))
        return base

    # ---- reindex (from PostgreSQL + report snapshots only) ---------------------------------------------
    def reindex(self, invoice_id: str | None = None) -> int:
        """Rebuild vectors from PostgreSQL only — no OCR, no file reads of the invoice binaries."""
        import json as _json
        from pathlib import Path

        from app.agents.indexing import delete_points, index_invoice

        s = self.ctx.settings
        total = 0
        for row in self.ctx.repo.all_invoices_for_index(invoice_id):
            iid = str(row["invoice_id"])
            report = self.ctx.repo.latest_report(iid)  # finalized invoices: the stored report
            if report is None:  # pending review: the latest report FILE ({invoice_id}.json, written by the agent)
                p = Path(s.reports_dir) / f"{iid}.json"
                if p.is_file():
                    report = _json.loads(p.read_text(encoding="utf-8"))
            delete_points(self.ctx.qdrant, s.qdrant_collection, iid)  # old source_type names / stale chunks go too
            text = row["translated_text"] or row["raw_extracted_text"] or ""
            total += index_invoice(self.ctx.qdrant, s.qdrant_collection, self.ctx.llm, invoice_id=iid,
                                   invoice_number=row["invoice_number"], vendor=row["vendor_name"], language=row["source_language"],
                                   text=text, report=report, chunk_chars=s.chunk_chars, overlap=s.chunk_overlap_chars)
        return total
