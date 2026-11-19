"""Redis RAG response cache: key format, TTL, isolation, SCAN-based invalidation."""
import hashlib

import fakeredis
import pytest

from app.core.rag_cache import RagCache, cache_key, normalize_question


def test_key_follows_the_required_format():
    q = "  Why was   THIS invoice flagged? "
    normalized = " ".join(q.lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    assert normalize_question(q) == "why was this invoice flagged?"
    assert cache_key("abc", q) == f"rag:abc:{digest}" and cache_key(None, q) == f"rag:all:{digest}"
    assert cache_key("abc", "why was this invoice flagged?") == cache_key("abc", q)  # normalisation makes them equal
    assert cache_key("abc", q, vendor="ACME") != cache_key("abc", q)


def test_miss_hit_and_ttl():
    r = fakeredis.FakeRedis()
    c = RagCache(r, 3600)
    assert c.get("inv", "q?") is None
    c.set("inv", "q?", {"answer": "a", "route": "open", "sources": [], "eval_id": "e"})
    assert c.get("inv", "Q?  ")["answer"] == "a"
    assert 3590 <= r.ttl(cache_key("inv", "q?")) <= 3600
    assert RagCache(r, 60).ttl == 60


def test_invoice_isolation_and_invalidation():
    r = fakeredis.FakeRedis()
    c = RagCache(r)
    for inv in ("a", "b", None):
        c.set(inv, "same question", {"answer": f"for {inv}"})
        c.set(inv, "other question", {"answer": f"for {inv} 2"})
    assert c.get("a", "same question")["answer"] == "for a" and c.get("b", "same question")["answer"] == "for b"
    r.set("llm:abc", "generic llm cache")
    res = c.invalidate_invoice("a")
    assert (res.invoice_keys, res.global_keys, res.total) == (2, 2, 4)
    assert c.get("a", "same question") is None and c.get(None, "same question") is None
    assert c.get("b", "same question") is not None and r.get("llm:abc") is not None


def test_invalidation_uses_scan_not_keys():
    r = fakeredis.FakeRedis()
    for i in range(1200):
        r.set(f"rag:big:{i}", "x")
    calls = {"keys": 0, "scan": 0}
    real_scan = r.scan_iter
    r.keys = lambda *a, **k: calls.__setitem__("keys", calls["keys"] + 1) or []  # type: ignore[method-assign]
    r.scan_iter = lambda *a, **k: calls.__setitem__("scan", calls["scan"] + 1) or real_scan(*a, **k)  # type: ignore[method-assign]
    assert RagCache(r).invalidate_invoice("big").invoice_keys == 1200
    assert calls["keys"] == 0 and calls["scan"] >= 1


def test_invalidation_failure_propagates_but_reads_and_writes_fail_open():
    class Broken:
        def scan_iter(self, *a, **k):
            raise ConnectionError("down")

        def get(self, *a):
            raise ConnectionError("down")

        def set(self, *a, **k):
            raise ConnectionError("down")

    c = RagCache(Broken())
    with pytest.raises(ConnectionError):
        c.invalidate_invoice("a")  # the caller must know the cache was NOT cleared
    assert c.get("a", "q") is None
    c.set("a", "q", {"answer": "x"})  # no exception
