# ================================================================
# AI INVOICE AUDITOR - RAG: LANGGRAPH
#
#   router -> sql -> END
#          -> retrieve -> augment -> generate -> reflect -> END
#                ^                                 |
#                +------------- retry <------------+  (score below threshold)
#
# The nodes live in app/agents/rag/:
#   router.py (router, sql), retrieval.py, augmentation.py,
#   generation.py, reflection.py (reflect, retry)
# ================================================================

from langgraph.graph import StateGraph, START, END

from app.agents.rag.router import router_node, sql_node
from app.agents.rag.retrieval import retrieval_node
from app.agents.rag.augmentation import augmentation_node
from app.agents.rag.generation import generation_node
from app.agents.rag.reflection import reflection_node, route_after_reflection, retry_node
from app.graphs.state import RAGState

# ================================================================
# STEP 19: BUILD LANGGRAPH
# ================================================================

def build_graph():

    workflow = StateGraph(RAGState)

    # STEP 1: Add nodes.

    workflow.add_node("router", router_node)

    workflow.add_node("sql", sql_node)

    workflow.add_node("retrieve", retrieval_node)

    workflow.add_node("augment", augmentation_node)

    workflow.add_node("generate", generation_node)

    workflow.add_node("reflect", reflection_node)

    workflow.add_node("retry", retry_node)

    # STEP 2: Start with router.

    workflow.add_edge(
        START,
        "router"
    )

    # STEP 3: Choose SQL or RAG path.

    workflow.add_conditional_edges(

        "router",

        lambda state: state["query_type"],

        {
            "sql": "sql",
            "rag": "retrieve"
        }
    )

    # STEP 4: SQL path.
    # An invoice still awaiting human review has no final values in
    # PostgreSQL yet: sql_node switches it to the RAG path.

    workflow.add_conditional_edges(

        "sql",

        lambda state: state["query_type"],

        {
            "sql": END,
            "rag": "retrieve"
        }
    )

    # STEP 5: RAG path.

    workflow.add_edge(
        "retrieve",
        "augment"
    )

    workflow.add_edge(
        "augment",
        "generate"
    )

    workflow.add_edge(
        "generate",
        "reflect"
    )

    # STEP 6: Reflection routing.

    workflow.add_conditional_edges(

        "reflect",

        route_after_reflection,

        {
            "end": END,
            "retry": "retry"
        }
    )

    # STEP 7: Retry returns to retrieval.

    workflow.add_edge(
        "retry",
        "retrieve"
    )

    return workflow.compile()
