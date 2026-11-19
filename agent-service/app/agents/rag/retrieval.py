# ================================================================
# AI INVOICE AUDITOR - RAG: RETRIEVAL AGENT
# ================================================================

from langchain_core.documents import Document

from qdrant_client.models import (
    Filter,
    FieldCondition,
    MatchValue
)

from app.agents.rag import setup
from app.graphs.state import RAGState

# ================================================================
# STEP 13: RETRIEVAL AGENT
# ================================================================

# The indexing agent (app/agents/indexing.py) stores each chunk in Qdrant with
# the payload: invoice_id, invoice_number, vendor, language, source_type, chunk_text.

def retrieval_node(state: RAGState):

    question = state["question"]

    # Increase candidate count during retry.

    k = setup.RETRIEVAL_K + (
        state["retry_count"] * 5
    )

    invoice_id = state.get("invoice_id")

    vendor = state.get("vendor")

    # Filter by invoice ID (and vendor) when provided.

    conditions = []

    if invoice_id:

        conditions.append(
            FieldCondition(
                key="invoice_id",
                match=MatchValue(
                    value=str(invoice_id)
                )
            )
        )

    if vendor:

        conditions.append(
            FieldCondition(
                key="vendor",
                match=MatchValue(
                    value=vendor
                )
            )
        )

    qdrant_filter = Filter(must=conditions) if conditions else None

    # Search Qdrant.

    query_vector = setup.embeddings.embed_query(question)

    results = setup.qdrant_client.query_points(
        collection_name=setup.COLLECTION_NAME,
        query=query_vector,
        query_filter=qdrant_filter,
        limit=k,
        with_payload=True
    )

    documents = [
        Document(
            page_content=point.payload.get("chunk_text", ""),
            metadata={**point.payload, "score": float(point.score)}
        )
        for point in results.points
    ]

    return {
        "retrieved_docs": documents
    }
