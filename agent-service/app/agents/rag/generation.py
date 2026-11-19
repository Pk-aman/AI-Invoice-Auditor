# ================================================================
# AI INVOICE AUDITOR - RAG: GENERATION AGENT
# ================================================================

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from app.agents.rag import setup
from app.graphs.state import RAGState

# ================================================================
# STEP 15: GENERATION AGENT
# ================================================================

# Answers are cached in Redis by RagService (app/services/rag_service.py)
# under rag:{invoice_id|all}:{sha256(question)}, so a repeated question
# skips the whole graph and the cache is cleared when an invoice is corrected.

NO_CONTEXT_ANSWER = (
    "I could not find relevant information "
    "for this question in the indexed invoices."
)


def generate_answer(question, context):

    prompt = ChatPromptTemplate.from_messages([

        (
            "system",

            """
            You are an AI Invoice Auditor assistant.

            Answer using ONLY the supplied invoice context.

            Rules:
            - Do not invent invoice information.
            - Do not assume missing values.
            - If information is unavailable, say so.
            - Explain discrepancies clearly.
            - Mention relevant invoice details.
            """
        ),

        (
            "human",

            """
            Context:

            {context}

            Question:

            {question}

            Provide a clear and accurate answer.
            """
        )

    ])

    chain = (
        prompt
        | setup.llm
        | StrOutputParser()
    )

    answer = chain.invoke({
        "context": context,
        "question": question
    })

    return answer


def generation_node(state: RAGState):

    # Do not generate an answer without context.

    if not state["context"]:

        return {
            "answer": NO_CONTEXT_ANSWER
        }

    answer = generate_answer(
        state["question"],
        state["context"]
    )

    return {
        "answer": answer
    }
