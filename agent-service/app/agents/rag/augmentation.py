# ================================================================
# AI INVOICE AUDITOR - RAG: RERANKER + AUGMENTATION AGENT
# ================================================================

import logging

from typing import List

from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate

from app.agents.rag import setup
from app.graphs.state import RAGState

log = logging.getLogger(__name__)

# ================================================================
# STEP 8: RERANKER (LLM)
# ================================================================

# Qdrant retrieves candidate chunks using vector similarity.
# The LLM reranks those chunks using question-document relevance,
# like a cross-encoder, but through LiteLLM (no PyTorch / Hugging Face).

class RerankScores(BaseModel):

    scores: List[float] = Field(
        description=(
            "One relevance score from 0 (useless) to 10 (directly answers "
            "the question) for EVERY passage, in the same order as the passages"
        )
    )


def rerank_documents(question, documents):

    # Create the numbered list of passages.

    passages = "\n\n".join(
        f"[{index}] {doc.page_content[:600]}"
        for index, doc in enumerate(documents, start=1)
    )

    prompt = ChatPromptTemplate.from_messages([

        (
            "system",

            """
            You rank text passages by how useful they are for answering
            a question about invoices. Give every passage a score.
            """
        ),

        (
            "human",

            """
            Question: {question}

            Passages ({count} in total):

            {passages}
            """
        )

    ])

    rerank_chain = (
        prompt
        | setup.llm.with_structured_output(RerankScores)
    )

    # Calculate relevance scores.

    try:

        scores = rerank_chain.invoke({
            "question": question,
            "count": len(documents),
            "passages": passages
        }).scores

        if len(scores) != len(documents):
            raise ValueError(f"{len(scores)} scores for {len(documents)} passages")

    except Exception:

        # Keep Qdrant's vector-similarity order.

        log.warning("LLM rerank failed; keeping the vector order", exc_info=True)

        scores = [
            doc.metadata.get("score", 0.0)
            for doc in documents
        ]

    # Combine documents with scores.

    ranked_documents = list(
        zip(documents, scores)
    )

    # Highest relevance score first.

    ranked_documents.sort(
        key=lambda item: item[1],
        reverse=True
    )

    return ranked_documents

# ================================================================
# STEP 14: AUGMENTATION AGENT
# ================================================================

def augmentation_node(state: RAGState):

    question = state["question"]

    documents = state["retrieved_docs"]

    if not documents:

        return {
            "context": "",
            "context_chunks": [],
            "sources": []
        }

    # STEP 1: Rerank retrieved documents.

    ranked_documents = rerank_documents(
        question,
        documents
    )

    # STEP 2: Select top relevant chunks.

    selected_documents = ranked_documents[
        :setup.FINAL_CONTEXT_K
    ]

    # STEP 3: Assemble final context.

    context_parts = []

    sources = []

    for doc, score in selected_documents:

        context_parts.append(
            f"""
            Source Type: {doc.metadata.get("source_type")}

            Invoice ID: {doc.metadata.get("invoice_id")}

            Vendor: {doc.metadata.get("vendor")}

            Relevance Score: {float(score):.4f}

            Content:
            {doc.page_content}
            """
        )

        # Shown under the answer in the UI.

        sources.append({
            "ref": f"S{len(sources) + 1}",
            "source_type": doc.metadata.get("source_type"),
            "invoice_id": doc.metadata.get("invoice_id"),
            "invoice_number": doc.metadata.get("invoice_number"),
            "vendor": doc.metadata.get("vendor"),
            "score": round(float(doc.metadata.get("score", 0.0)), 4),
            "rerank_score": round(float(score), 4),
            "chunk_text": doc.page_content
        })

    context = "\n\n".join(context_parts)

    return {
        "context": context,
        "context_chunks": [doc.page_content for doc, _ in selected_documents],
        "sources": sources
    }
