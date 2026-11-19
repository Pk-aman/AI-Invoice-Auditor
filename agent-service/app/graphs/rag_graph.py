"""LangGraph RAG pipeline: route -> (sql | retrieve -> augment -> generate). Reflection (RAG Triad) runs
after the answer is returned (see services/rag_service.py + services/ragas_evaluator.py) because judging with an LLM is slow."""
from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from app.agents.rag import augmentation, generation, retrieval, router
from app.core.context import AppContext
from app.graphs.state import RagState

log = logging.getLogger(__name__)


def build_rag_graph(ctx: AppContext):
    s = ctx.settings

    def route_node(state: RagState) -> dict:
        plan = router.route(state["question"], ctx.llm, state.get("invoice_id"))
        return {"route": plan.kind, "template": plan.template, "params": plan.params}

    def sql_node(state: RagState) -> dict:
        plan = router.RoutePlan("structured", state["template"], state["params"])
        sql, _ = router.TEMPLATES[state["template"]]  # type: ignore[index]
        rows = ctx.repo.run_template(sql, router.sql_params(plan))
        return {"rows": rows, "answer": router.format_rows(state["template"], rows),  # type: ignore[arg-type]
                "sources": [{"ref": "DB", "source_type": "database", "chunk_text": router.format_rows(state["template"], rows),  # type: ignore[arg-type]
                             "template": state["template"], "rows": len(rows)}]}

    def retrieve_node(state: RagState) -> dict:
        hits = retrieval.retrieve(ctx.qdrant, s.qdrant_collection, ctx.llm, state["question"], invoice_id=state.get("invoice_id"),
                                  vendor=state.get("vendor"), top_k=s.rag_top_k)
        return {"hits": hits}

    def augment_node(state: RagState) -> dict:
        c = augmentation.assemble(state["question"], state.get("hits", []), s.rag_max_context_tokens)
        return {"context": c.text, "sources": c.sources, "context_chunks": c.chunks}

    def generate_node(state: RagState) -> dict:
        return {"answer": generation.generate(state["question"], state.get("context", ""), ctx.llm)}

    g = StateGraph(RagState)
    g.add_node("route", route_node)
    g.add_node("sql", sql_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("augment", augment_node)
    g.add_node("generate", generate_node)
    g.set_entry_point("route")
    g.add_conditional_edges("route", lambda st: st["route"], {"structured": "sql", "open": "retrieve"})
    g.add_edge("sql", END)
    g.add_edge("retrieve", "augment")
    g.add_edge("augment", "generate")
    g.add_edge("generate", END)
    return g.compile()
