"""Redis cache for RAG responses (separate from the generic LLM-response cache `llm:*`).

Key:   rag:{invoice_id or 'all'}:{sha256(normalised question)}     TTL: RAG_CACHE_TTL_S (default 3600 s)
Value: JSON {answer, route, sources, eval_id}  (evaluation scores live in PostgreSQL and are looked up by eval_id)

Vendor-scoped questions hash the vendor into the question part so two vendors never share an answer.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

SCAN_COUNT = 500


def normalize_question(question: str) -> str:
    return " ".join(question.lower().split())


def question_hash(question: str, vendor: str | None = None) -> str:
    normalized_question = normalize_question(question)
    if vendor:
        normalized_question = f"{normalize_question(vendor)}\x1f{normalized_question}"
    return hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()


def cache_key(invoice_id: str | None, question: str, vendor: str | None = None) -> str:
    return f"rag:{invoice_id or 'all'}:{question_hash(question, vendor)}"


@dataclass
class Invalidation:
    invoice_keys: int
    global_keys: int

    @property
    def total(self) -> int:
        return self.invoice_keys + self.global_keys


class RagCache:
    def __init__(self, redis_client, ttl_s: int = 3600):
        self.redis = redis_client
        self.ttl = ttl_s

    def get(self, invoice_id: str | None, question: str, vendor: str | None = None) -> dict | None:
        try:
            raw = self.redis.get(cache_key(invoice_id, question, vendor))
        except Exception:  # noqa: BLE001 - a broken cache must never break answering
            log.warning("rag cache read failed", exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set(self, invoice_id: str | None, question: str, payload: dict, vendor: str | None = None) -> None:
        try:
            self.redis.set(cache_key(invoice_id, question, vendor), json.dumps(payload, ensure_ascii=False, default=str), ex=self.ttl)
        except Exception:  # noqa: BLE001
            log.warning("rag cache write failed", exc_info=True)

    def _delete_matching(self, pattern: str) -> int:
        deleted, batch = 0, []
        for key in self.redis.scan_iter(match=pattern, count=SCAN_COUNT):  # SCAN, never the blocking KEYS
            batch.append(key)
            if len(batch) >= SCAN_COUNT:
                deleted += int(self.redis.delete(*batch))
                batch = []
        if batch:
            deleted += int(self.redis.delete(*batch))
        return deleted

    def invalidate_invoice(self, invoice_id: str) -> Invalidation:
        """Delete every `rag:{invoice_id}:*` key. Cross-invoice answers (`rag:all:*`) also quote this invoice's
        report, so they are dropped too. Raises on Redis failure: the caller must not claim the cache was cleared."""
        mine = self._delete_matching(f"rag:{invoice_id}:*")
        shared = self._delete_matching("rag:all:*")
        log.info("rag cache invalidated", extra={"invoice_id": invoice_id, "deleted_invoice_keys": mine, "deleted_global_keys": shared})
        return Invalidation(mine, shared)
