"""RAG facade: answer a question, then (asynchronously) score it with the RAG Triad."""
from __future__ import annotations

import json
import logging
import threading

from app.agents.rag.reflection import reflect
from app.core.context import AppContext
from app.graphs.rag_graph import build_rag_graph

log = logging.getLogger(__name__)

_STATE_TTL_S = 3600


class RagService:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self.graph = build_rag_graph(ctx)

    def _set_state(self, eval_id: str, state: str, error: str | None = None) -> None:
        try:
            self.ctx.redis.set(f"rag_eval:{eval_id}", json.dumps({"state": state, "error": error}), ex=_STATE_TTL_S)
        except Exception:  # noqa: BLE001
            log.warning("cannot record reflection state in redis", exc_info=True)

    def _get_state(self, eval_id: str) -> dict | None:
        try:
            raw = self.ctx.redis.get(f"rag_eval:{eval_id}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            return None

    def query(self, question: str, invoice_id: str | None = None, vendor: str | None = None) -> dict:
        out = self.graph.invoke({"question": question, "invoice_id": invoice_id, "vendor": vendor})
        answer, route, sources = out.get("answer", ""), out.get("route", "open"), out.get("sources", [])
        eval_id = None
        if self.ctx.settings.reflection_enabled and route == "open" and out.get("context"):
            eval_id = self.ctx.repo.create_rag_eval(question, answer, route)
            self._set_state(eval_id, "pending")
            threading.Thread(target=self._reflect, args=(eval_id, question, answer, out["context"]), daemon=True, name="rag-reflect").start()
        return {"answer": answer, "route": route, "sources": sources, "eval_id": eval_id}

    def _reflect(self, eval_id: str, question: str, answer: str, context: str) -> None:
        try:
            scores = reflect(question, answer, context, self.ctx.llm)
            self.ctx.repo.save_rag_scores(eval_id, scores.relevance, scores.groundedness, scores.context_relevance)
            self._set_state(eval_id, "done")
        except Exception as exc:  # noqa: BLE001
            log.exception("reflection failed", extra={"eval_id": eval_id})
            self._set_state(eval_id, "failed", f"{type(exc).__name__}: {exc}")

    def evaluation(self, eval_id: str) -> dict | None:
        row = self.ctx.repo.get_rag_eval(eval_id)
        if row is None:
            return None
        if row["relevance"] is not None:
            return {"state": "done", "relevance": float(row["relevance"]), "groundedness": float(row["groundedness"]),
                    "context_relevance": float(row["context_relevance"])}
        st = self._get_state(eval_id) or {"state": "pending", "error": None}
        return {"state": st["state"], "error": st.get("error"), "relevance": None, "groundedness": None, "context_relevance": None}

    def reindex(self, invoice_id: str | None = None) -> int:
        """Rebuild vectors from PostgreSQL only — no OCR, no file reads."""
        import json as _json
        from pathlib import Path

        from app.agents.indexing import index_invoice

        s = self.ctx.settings
        total = 0
        for row in self.ctx.repo.all_invoices_for_index(invoice_id):
            report = None
            if row["report_path"]:
                p = Path(s.reports_dir) / Path(row["report_path"]).with_suffix(".json").name
                if p.is_file():
                    report = _json.loads(p.read_text(encoding="utf-8"))
            text = row["translated_text"] or row["raw_extracted_text"] or ""
            total += index_invoice(self.ctx.qdrant, s.qdrant_collection, self.ctx.llm, invoice_id=str(row["invoice_id"]),
                                   invoice_number=row["invoice_number"], vendor=row["vendor_name"], language=row["source_language"],
                                   text=text, report=report, chunk_chars=s.chunk_chars, overlap=s.chunk_overlap_chars)
        return total
