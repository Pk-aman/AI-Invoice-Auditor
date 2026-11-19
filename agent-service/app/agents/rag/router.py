# ================================================================
# AI INVOICE AUDITOR - RAG: QUERY ROUTER + SQL AGENT
# ================================================================

import json
import logging

from decimal import Decimal

from typing import Literal, Optional

from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate

from app.agents.rag import setup
from app.graphs.state import RAGState

log = logging.getLogger(__name__)

# ================================================================
# STEP 10: DEFINE STRUCTURED QUERY ROUTER
# ================================================================

class QueryRoute(BaseModel):

    query_type: Literal["sql", "rag"] = Field(
        description=(
            "Use sql for exact structured values, "
            "totals, counts and statuses. "
            "Use rag for explanations and comparisons."
        )
    )

    sql_action: Optional[
        Literal[
            # one invoice
            "total_amount",
            "validation_status",
            "discrepancy_count",
            "discrepancies",
            # all invoices
            "invoice_count",
            "invoices_by_status",
            "invoices_by_recommendation",
            "invoices_by_vendor",
            "totals_by_currency",
            "invoices_needing_attention",
            "rejected_invoices",
            "approved_invoices"
        ]
    ] = None

    invoice_number: Optional[str] = Field(
        default=None,
        description="The invoice number if the question names one, e.g. INV-1001"
    )

# ================================================================
# STEP 11: QUERY ROUTER NODE
# ================================================================

def router_node(state: RAGState):

    prompt = ChatPromptTemplate.from_messages([

        (
            "system",

            """
            You are a query router for an AI Invoice Auditor. Decide how a question is answered.

            query_type = "rag" when the question asks for an explanation, a reason, a description,
            a summary, a comparison or "what issues / problems". These are answered from documents.
            query_type = "sql" ONLY when the question asks for one exact value, a count or a list
            that a database table holds (amount, status, number of discrepancies, list of invoices).

            Examples:
            "Why was this invoice flagged?" -> query_type=rag
            "Explain the discrepancies of this invoice" -> query_type=rag
            "What issues were detected?" -> query_type=rag
            "Summarize my invoices." -> query_type=rag
            "Describe the problem with the unit price" -> query_type=rag
            "Which items or products were invoiced?" -> query_type=rag
            "What is the total amount of this invoice?" -> query_type=sql, sql_action=total_amount
            "What is the status of this invoice?" -> query_type=sql, sql_action=validation_status
            "What is the recommendation for invoice INV-1001?" -> query_type=sql, sql_action=validation_status, invoice_number=INV-1001
            "How many discrepancies does this invoice have?" -> query_type=sql, sql_action=discrepancy_count
            "List the discrepancies of this invoice" -> query_type=sql, sql_action=discrepancies
            "How many invoices are there?" -> query_type=sql, sql_action=invoice_count
            "How many invoices per status / recommendation / vendor?" -> query_type=sql, sql_action=invoices_by_status or invoices_by_recommendation or invoices_by_vendor
            "What are the totals by currency?" -> query_type=sql, sql_action=totals_by_currency
            "Which invoices need attention?" -> query_type=sql, sql_action=invoices_needing_attention
            "Which invoices were rejected / approved?" -> query_type=sql, sql_action=rejected_invoices or approved_invoices

            If the question names an invoice number, copy it into invoice_number.
            """
        ),

        (
            "human",
            "{question}"
        )

    ])

    router_chain = (
        prompt
        | setup.llm.with_structured_output(QueryRoute)
    )

    try:

        result = router_chain.invoke({
            "question": state["question"]
        })

    except Exception:

        # If the router fails, answer from the documents.

        log.warning("router failed; using rag", exc_info=True)

        return {
            "query_type": "rag",
            "sql_action": "",
            "invoice_number": ""
        }

    query_type = result.query_type

    # SQL without a known action cannot be answered: use RAG.

    if query_type == "sql" and result.sql_action not in QUERIES:
        query_type = "rag"

    return {
        "query_type": query_type,
        "sql_action": result.sql_action or "",
        "invoice_number": result.invoice_number or ""
    }

# ================================================================
# STEP 12: SQL AGENT
# ================================================================

# Instead of executing arbitrary SQL generated by an LLM,
# we use predefined parameterized queries.
# This is safer and easier to maintain.

# Queries about ONE invoice (parameter: invoice_id).
# Discrepancies of earlier validation runs are kept but marked superseded.

INVOICE_QUERIES = {

    "total_amount": """
        SELECT
            invoice_id,
            invoice_number,
            vendor_name,
            total_amount,
            currency

        FROM audit.invoice_audit

        WHERE invoice_id = %s
    """,

    "validation_status": """
        SELECT
            invoice_id,
            invoice_number,
            status,
            validation_status,
            recommendation

        FROM audit.invoice_audit

        WHERE invoice_id = %s
    """,

    "discrepancy_count": """
        SELECT
            COUNT(*) AS discrepancy_count

        FROM audit.discrepancies

        WHERE invoice_id = %s
          AND superseded = FALSE
    """,

    "discrepancies": """
        SELECT
            field_name,
            invoice_value,
            erp_value,
            deviation_pct,
            severity

        FROM audit.discrepancies

        WHERE invoice_id = %s
          AND superseded = FALSE

        ORDER BY id
    """
}

# Queries about ALL invoices (no parameters).

ALL_INVOICE_QUERIES = {

    "invoice_count": """
        SELECT
            COUNT(*) AS invoices

        FROM audit.invoice_audit
    """,

    "invoices_by_status": """
        SELECT
            COALESCE(validation_status, 'pending') AS validation_status,
            COUNT(*) AS invoices

        FROM audit.invoice_audit

        GROUP BY 1
        ORDER BY 2 DESC
    """,

    "invoices_by_recommendation": """
        SELECT
            COALESCE(recommendation, 'pending') AS recommendation,
            COUNT(*) AS invoices

        FROM audit.invoice_audit

        GROUP BY 1
        ORDER BY 2 DESC
    """,

    "invoices_by_vendor": """
        SELECT
            COALESCE(vendor_name, '(unknown)') AS vendor_name,
            COUNT(*) AS invoices

        FROM audit.invoice_audit

        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 50
    """,

    "totals_by_currency": """
        SELECT
            COALESCE(currency, '(none)') AS currency,
            COUNT(*) AS invoices,
            COALESCE(SUM(total_amount), 0) AS total_amount

        FROM audit.invoice_audit

        GROUP BY 1
        ORDER BY 3 DESC
    """,

    "invoices_needing_attention": """
        SELECT
            invoice_number,
            vendor_name,
            total_amount,
            currency,
            recommendation,
            processed_at

        FROM audit.invoice_audit

        WHERE recommendation IN ('review', 'reject')

        ORDER BY processed_at DESC
        LIMIT 50
    """,

    "rejected_invoices": """
        SELECT
            invoice_number,
            vendor_name,
            total_amount,
            currency,
            processed_at

        FROM audit.invoice_audit

        WHERE recommendation = 'reject'

        ORDER BY processed_at DESC
        LIMIT 50
    """,

    "approved_invoices": """
        SELECT
            invoice_number,
            vendor_name,
            total_amount,
            currency,
            processed_at

        FROM audit.invoice_audit

        WHERE recommendation = 'approve'

        ORDER BY processed_at DESC
        LIMIT 50
    """
}

QUERIES = {**INVOICE_QUERIES, **ALL_INVOICE_QUERIES}

AWAITING_REVIEW_NOTE = (
    "Note: this invoice is still awaiting human review, so its final "
    "figures are not recorded yet. This answer comes from the AI audit report."
)


def run_query(sql, params=()):

    with setup.get_db_connection() as conn:

        with conn.cursor() as cursor:

            cursor.execute(sql, params)

            rows = cursor.fetchall()

    # PostgreSQL NUMERIC -> number (so amounts print as 960.0, not "960.00").

    return [
        {key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()}
        for row in rows
    ]


def sql_node(state: RAGState):

    action = state["sql_action"]

    rows = []

    if action in INVOICE_QUERIES:

        invoice_id = state.get("invoice_id")

        # The question may name the invoice instead of selecting it.

        if not invoice_id and state.get("invoice_number"):

            found = run_query(
                "SELECT invoice_id FROM audit.invoice_audit WHERE invoice_number = %s LIMIT 1",
                (state["invoice_number"],)
            )

            if found:
                invoice_id = str(found[0]["invoice_id"])

        if not invoice_id:

            return {
                "answer": (
                    "Please provide an invoice ID for this "
                    "structured invoice query."
                ),
                "sources": []
            }

        # Invoice values are written to PostgreSQL only after the human review.
        # Until then, answer from the AI audit report (RAG).

        invoice = run_query(
            "SELECT status FROM audit.invoice_audit WHERE invoice_id = %s",
            (invoice_id,)
        )

        if not invoice:

            return {
                "answer": "That invoice was not found.",
                "sources": []
            }

        if invoice[0]["status"] == "INGESTED":

            return {
                "query_type": "rag",
                "invoice_id": invoice_id,
                "note": AWAITING_REVIEW_NOTE
            }

        rows = run_query(QUERIES[action], (invoice_id,))

    elif action in ALL_INVOICE_QUERIES:

        rows = run_query(QUERIES[action])

    else:

        return {
            "answer": "Unsupported structured query.",
            "sources": []
        }

    # Execute approved parameterized query and return the rows.

    answer = json.dumps(
        rows,
        default=str,
        indent=4
    )

    # Shown as a code block in the UI.

    answer = f"```json\n{answer}\n```"

    return {
        "answer": answer,
        "sources": [
            {
                "ref": "DB",
                "source_type": "database",
                "chunk_text": answer,
                "sql_action": action,
                "rows": len(rows)
            }
        ]
    }
