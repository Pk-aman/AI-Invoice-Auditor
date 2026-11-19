"""Retrieval agent: embed the question, search Qdrant with optional invoice/vendor/source filters."""
from __future__ import annotations

import re
from dataclasses import dataclass

from qdrant_client import QdrantClient

from app.agents.indexing import SOURCE_REPORT
from app.clients.qdrant_client import invoice_filter
from app.core.llm import LLM

_REPORT_BIAS = re.compile(r"\bwhy\b|flag|reject|discrepan|mismatch|recommend|review|problem|wrong|error", re.I)


@dataclass
class Hit:
    id: str
    score: float
    text: str
    payload: dict


def _search(client: QdrantClient, collection: str, vector: list[float], flt, limit: int) -> list[Hit]:
    res = client.query_points(collection, query=vector, query_filter=flt, limit=limit, with_payload=True)
    return [Hit(str(p.id), float(p.score), str(p.payload.get("chunk_text", "")), dict(p.payload)) for p in res.points]


def retrieve(client: QdrantClient, collection: str, llm: LLM, question: str, *, invoice_id: str | None = None,
             vendor: str | None = None, source_type: str | None = None, top_k: int = 12) -> list[Hit]:
    vector = llm.embed([question])[0]
    hits: list[Hit] = []
    if source_type is None and _REPORT_BIAS.search(question):
        # "why was it flagged" is answered by the report, not the invoice text
        hits = _search(client, collection, vector, invoice_filter(invoice_id, vendor, SOURCE_REPORT), top_k)
    if len(hits) < 3:
        seen = {h.id for h in hits}
        extra = _search(client, collection, vector, invoice_filter(invoice_id, vendor, source_type), top_k)
        hits += [h for h in extra if h.id not in seen]
    return hits[: top_k * 2]
