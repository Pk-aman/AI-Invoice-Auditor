"""Augmentation agent: hybrid rerank (vector rank + BM25 rank fused with RRF) and context assembly."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.agents.rag.retrieval import Hit

RRF_K = 60


@dataclass
class Context:
    text: str  # numbered passages, ready for the prompt
    sources: list[dict]


def _tokens(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def rerank(question: str, hits: list[Hit]) -> list[Hit]:
    if len(hits) <= 1:
        return hits
    vec_rank = {h.id: r for r, h in enumerate(sorted(hits, key=lambda x: -x.score))}
    bm25 = BM25Okapi([_tokens(h.text) or ["_"] for h in hits])
    scores = bm25.get_scores(_tokens(question) or ["_"])
    lex_rank = {hits[i].id: r for r, i in enumerate(sorted(range(len(hits)), key=lambda i: -scores[i]))}
    fused = {h.id: 1 / (RRF_K + vec_rank[h.id]) + 1 / (RRF_K + lex_rank[h.id]) for h in hits}
    return sorted(hits, key=lambda h: -fused[h.id])


def assemble(question: str, hits: list[Hit], max_tokens: int = 3000) -> Context:
    budget = max_tokens * 4  # ~4 chars per token
    ordered = rerank(question, hits)
    parts: list[str] = []
    sources: list[dict] = []
    used = 0
    for h in ordered:
        if used + len(h.text) > budget and parts:
            break
        n = len(parts) + 1
        p = h.payload
        parts.append(f"[S{n}] (invoice {p.get('invoice_number') or p.get('invoice_id')}, {p.get('source_type')}) {h.text}")
        sources.append({"ref": f"S{n}", "invoice_id": p.get("invoice_id"), "invoice_number": p.get("invoice_number"),
                        "source_type": p.get("source_type"), "vendor": p.get("vendor"), "score": round(h.score, 4),
                        "excerpt": h.text[:300]})
        used += len(h.text)
    return Context("\n\n".join(parts), sources)
