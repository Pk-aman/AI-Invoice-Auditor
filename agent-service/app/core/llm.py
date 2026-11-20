"""LLM gateway: LiteLLM (Bedrock Nova Lite by default) behind an injectable interface + Redis cache.

Everything in the app depends on the `LLM` protocol, never on litellm directly, so tests inject a stub
and no AWS call is ever made from tests.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Protocol

from app.core.config import Settings
from app.core.observability import get_tracer

log = logging.getLogger(__name__)

ROLES = ("reasoning", "translation", "generation")


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    def complete(self, role: str, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMClient:
    """Calls LiteLLM. Credentials come from the process environment (AWS_* for Bedrock)."""

    def __init__(self, settings: Settings):
        self.s = settings
        self.models = {
            "reasoning": settings.llm_reasoning_model,
            "translation": settings.llm_translation_model,
            "generation": settings.llm_generation_model,
        }

    def model_for(self, role: str) -> str:
        if role not in self.models:
            raise LLMError(f"unknown LLM role {role!r}")
        return self.models[role]

    def _extra(self, model: str) -> dict:
        return {"api_base": self.s.ollama_base_url} if model.startswith("ollama") else {}

    def complete(self, role: str, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        import litellm

        model = self.model_for(role)
        started = time.perf_counter()
        try:
            resp = litellm.completion(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=self.s.llm_temperature,
                timeout=self.s.llm_timeout_s,
                num_retries=2,
                max_tokens=max_tokens or 2048,
                **({"response_format": {"type": "json_object"}} if json_mode and model.startswith("ollama") else {}),
                **self._extra(model),
            )
        except Exception as exc:  # noqa: BLE001 - surface any provider failure uniformly
            get_tracer().generation(name=f"llm.{role}", model=model, latency_s=time.perf_counter() - started,
                                    error=type(exc).__name__, role=role)
            raise LLMError(f"LLM call failed ({model}): {type(exc).__name__}: {exc}") from exc
        usage = getattr(resp, "usage", None)
        get_tracer().generation(
            name=f"llm.{role}", model=model, latency_s=time.perf_counter() - started, role=role,
            usage={"input": int(getattr(usage, "prompt_tokens", 0) or 0), "output": int(getattr(usage, "completion_tokens", 0) or 0)}
            if usage else None, prompt_chars=len(system) + len(user))  # sizes only: prompt/completion text is never traced
        content = resp.choices[0].message.content
        if not content:
            raise LLMError(f"LLM returned empty content ({model})")
        return str(content)

    def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        out: list[list[float]] = []
        model = self.s.embedding_model
        started = time.perf_counter()
        for t in texts:  # Titan embeds one input per request
            try:
                resp = litellm.embedding(model=model, input=[t], timeout=self.s.llm_timeout_s, **self._extra(model))
            except Exception as exc:  # noqa: BLE001
                get_tracer().generation(name="llm.embedding", model=model, latency_s=time.perf_counter() - started,
                                        error=type(exc).__name__)
                raise LLMError(f"embedding call failed ({model}): {type(exc).__name__}: {exc}") from exc
            out.append(list(resp.data[0]["embedding"]))
        get_tracer().generation(name="llm.embedding", model=model, latency_s=time.perf_counter() - started, inputs=len(texts))
        return out


class CachedLLM:
    """Redis response cache. Key = llm:{sha256(...)}. Cache failures never break a request (fail-open)."""

    def __init__(self, inner: LLM, redis_client, ttl_s: int, namespace: str = ""):
        self.inner = inner
        self.redis = redis_client
        self.ttl = ttl_s
        self.ns = namespace

    @staticmethod
    def key(*parts: object) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return "llm:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _get(self, key: str) -> str | None:
        try:
            val = self.redis.get(key)
            return val.decode("utf-8") if isinstance(val, bytes) else val
        except Exception:  # noqa: BLE001
            log.warning("redis cache read failed", exc_info=True)
            return None

    def _set(self, key: str, value: str) -> None:
        try:
            self.redis.set(key, value, ex=self.ttl)
        except Exception:  # noqa: BLE001
            log.warning("redis cache write failed", exc_info=True)

    def complete(self, role: str, system: str, user: str, *, json_mode: bool = False, max_tokens: int | None = None) -> str:
        model = getattr(self.inner, "models", {}).get(role, role)
        k = self.key("complete", self.ns, model, system, user, json_mode, max_tokens)
        hit = self._get(k)
        if hit is not None:
            log.debug("llm cache hit", extra={"role": role})
            return hit
        result = self.inner.complete(role, system, user, json_mode=json_mode, max_tokens=max_tokens)
        self._set(k, result)
        return result

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = getattr(getattr(self.inner, "s", None), "embedding_model", "embed")
        keys = [self.key("embed", self.ns, model, t) for t in texts]
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        for i, k in enumerate(keys):
            hit = self._get(k)
            if hit is None:
                missing.append(i)
            else:
                out[i] = json.loads(hit)
        if missing:
            fresh = self.inner.embed([texts[i] for i in missing])
            for i, vec in zip(missing, fresh, strict=True):
                out[i] = vec
                self._set(keys[i], json.dumps(vec))
        return [v for v in out if v is not None]


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> dict:
    """Parse the first JSON object in an LLM reply (tolerates code fences and prose)."""
    candidates = [text.strip()]
    candidates += _JSON_FENCE.findall(text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            val = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(val, dict):
            return val
    raise LLMError("LLM reply did not contain a JSON object")


def llm_config_problem(s: Settings) -> str | None:
    """Return a human-readable problem with LLM configuration, or None when it looks usable."""
    placeholder = ("", "REPLACE_ME")
    models = {s.llm_reasoning_model, s.llm_translation_model, s.llm_generation_model, s.embedding_model}
    if any(m.startswith("bedrock/") for m in models):
        for name, val in (("AWS_ACCESS_KEY_ID", s.aws_access_key_id), ("AWS_SECRET_ACCESS_KEY", s.aws_secret_access_key)):
            if val in placeholder or val.startswith("<") or val.startswith("REPLACE_ME"):
                return f"{name} is not configured (agent-service/.env) but a bedrock/* model is selected"
    return None
