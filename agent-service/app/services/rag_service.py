# ================================================================
# AI INVOICE AUDITOR - RAG: INVOKE THE RAG GRAPH
# ================================================================

# POST /rag/query (app/api/routes.py) -> RagService.query -> ask_invoice_question
#
# Public response (see api/schemas.py::RagAnswer):
#   {invoice_id, answer, cache_hit, ragas_metrics{context_relevance, faithfulness, answer_relevance,
#    low_confidence_warning, state}, sources[{source_type, chunk_text, ...}], route, eval_id, retry_count}
#
# RAGAS runs inside the graph (reflect node), so the scores come back with the answer
# (state "done", or "failed" with the reason) and are stored in audit.rag_evaluations;
# GET /rag/evaluations/{eval_id} returns them again.

import logging

from app.agents.rag import setup
from app.core.context import AppContext
from app.core.rag_cache import RagCache
from app.graphs.rag_graph import build_graph

log = logging.getLogger(__name__)

rag_graph = None

# ================================================================
# STEP 20: INVOKE THE RAG GRAPH
# ================================================================

def ask_invoice_question(
    question,
    invoice_id=None,
    vendor=None
):

    initial_state = {

        "question": question,

        "invoice_id": invoice_id,

        "vendor": vendor,

        "query_type": "",

        "sql_action": "",

        "invoice_number": "",

        "note": "",

        "retrieved_docs": [],

        "context": "",

        "context_chunks": [],

        "sources": [],

        "answer": "",

        "scores": {},

        "passed": False,

        "retry_count": 0,

        "evaluation_error": ""
    }

    # Execute LangGraph.

    result = rag_graph.invoke(initial_state)

    return {

        "question": question,

        "answer": result["answer"],

        "query_type": result["query_type"],

        "note": result["note"],

        "sources": result["sources"],

        "context_chunks": result["context_chunks"],

        "scores": result["scores"],

        "evaluation_passed": result["passed"],

        "retry_count": result["retry_count"],

        "evaluation_error": result["evaluation_error"]
    }

# ================================================================
# RAG SERVICE (used by the FastAPI routes)
# ================================================================

class RagService:

    def __init__(self, ctx: AppContext):

        global rag_graph

        self.ctx = ctx

        setup.init(ctx)

        rag_graph = build_graph()

        self.cache = RagCache(ctx.redis, ctx.settings.rag_cache_ttl_s)

    # ---- query ----------------------------------------------------------------------------------

    def query(self, question: str, invoice_id: str | None = None, vendor: str | None = None) -> dict:

        # Same question for the same invoice -> cached answer (cleared when the invoice is corrected).

        cached = self.cache.get(invoice_id, question, vendor)

        if cached is not None:
            return self._response(invoice_id, cached, cache_hit=True)

        result = ask_invoice_question(question, invoice_id, vendor)

        route = "structured" if result["query_type"] == "sql" else "open"

        answer = result["answer"]

        if result["note"]:
            answer = f"{result['note']}\n\n{answer}"

        payload = {"answer": answer, "route": route, "sources": result["sources"], "eval_id": None,
                   "retry_count": result["retry_count"]}

        # SQL answers change with every new invoice: never cached, no RAGAS.

        if route == "open" and result["context_chunks"]:

            if self.ctx.settings.reflection_enabled:
                payload["eval_id"] = self._save_evaluation(question, answer, route, invoice_id, result)

            # "awaiting review" answers go stale as soon as the review finishes.

            if not result["note"]:
                self.cache.set(invoice_id, question, payload, vendor)

        return self._response(invoice_id, payload, cache_hit=False)

    def _save_evaluation(self, question: str, answer: str, route: str, invoice_id: str | None, result: dict) -> str:

        repo = self.ctx.repo

        eval_id = repo.create_rag_eval(question, answer, route, invoice_id)

        scores = result["scores"]

        if scores:
            repo.save_rag_scores(eval_id, context_relevance=scores["context_relevance"], faithfulness=scores["faithfulness"],
                                 answer_relevance=scores["answer_relevance"], low_confidence=not result["evaluation_passed"])
        else:
            repo.fail_rag_eval(eval_id, result["evaluation_error"] or "RAGAS evaluation did not run")

        return eval_id

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
                "route": payload.get("route", "open"), "eval_id": eval_id, "retry_count": payload.get("retry_count", 0)}

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
