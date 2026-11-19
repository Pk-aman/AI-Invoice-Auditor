"""Optional LangFuse observability.

Design rules
  * Disabled by default (LANGFUSE_ENABLED=false) and fully optional: no LangFuse import happens when disabled.
  * Observability can never change a business outcome: every call into the SDK is wrapped, failures are logged at
    DEBUG/WARNING and swallowed. A span's own body is NOT swallowed (exceptions in the traced code propagate).
  * Nothing sensitive is sent: spans carry ids, stage names, counts, lengths, scores, model names and latencies,
    never invoice text, prompts, completions or credentials.
  * Correlation: the LangFuse trace id is derived deterministically from the invoice id (`trace_id_for`), so the
    ingestion run, every review round and finalization of one invoice land in the same trace; request ids and
    invoice ids are attached as metadata and appear in the JSON logs (`correlation_id`, `request_id`).

Enable:  LANGFUSE_ENABLED=true  LANGFUSE_PUBLIC_KEY=pk-...  LANGFUSE_SECRET_KEY=sk-...  LANGFUSE_HOST=https://...
Disable: LANGFUSE_ENABLED=false (default) or leave the keys empty.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import time
from collections.abc import Iterator
from typing import Any

from app.core.logging import correlation_id, request_id

log = logging.getLogger(__name__)


def trace_id_for(invoice_id: str) -> str:
    """Deterministic 32-hex trace id (same format LangFuse uses) so all runs of one invoice share a trace."""
    return hashlib.sha256(f"invoice:{invoice_id}".encode()).hexdigest()[:32]


class SpanHandle:
    """What traced code sees: record metadata/scores on the current span. All methods are no-fail."""

    def __init__(self, span: Any | None = None):
        self._span = span

    def update(self, **fields: Any) -> None:
        if self._span is None:
            return
        try:
            self._span.update(**fields)
        except Exception:  # noqa: BLE001
            log.debug("langfuse span update failed", exc_info=True)

    def score(self, name: str, value: float) -> None:
        if self._span is None:
            return
        try:
            self._span.score_trace(name=name, value=float(value))
        except Exception:  # noqa: BLE001
            log.debug("langfuse score failed", exc_info=True)


class Tracer:
    """No-op by default; the LangFuse-backed subclass is created only when enabled and configured."""

    enabled = False

    @contextlib.contextmanager
    def span(self, name: str, *, invoice_id: str | None = None, **metadata: Any) -> Iterator[SpanHandle]:
        yield SpanHandle()

    def generation(self, *, name: str, model: str, latency_s: float, usage: dict | None = None, error: str | None = None,
                   **metadata: Any) -> None:
        return None

    def flush(self) -> None:
        return None


class LangfuseTracer(Tracer):
    enabled = True

    def __init__(self, public_key: str, secret_key: str, host: str):
        from langfuse import Langfuse  # imported lazily: only when enabled

        self.client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    @staticmethod
    def _meta(invoice_id: str | None, metadata: dict) -> dict:
        out = {k: v for k, v in metadata.items() if v is not None}
        if invoice_id:
            out["invoice_id"] = invoice_id
        if rid := request_id.get():
            out["request_id"] = rid
        if cid := correlation_id.get():
            out.setdefault("correlation_id", cid)
        return out

    @contextlib.contextmanager
    def span(self, name: str, *, invoice_id: str | None = None, **metadata: Any) -> Iterator[SpanHandle]:
        try:
            ctx_kwargs: dict[str, Any] = {"name": name, "metadata": self._meta(invoice_id, metadata)}
            if invoice_id:
                ctx_kwargs["trace_context"] = {"trace_id": trace_id_for(invoice_id)}
            manager = self.client.start_as_current_span(**ctx_kwargs)
            span = manager.__enter__()
        except Exception:  # noqa: BLE001 - observability must not break the business flow
            log.warning("langfuse span could not be started; continuing untraced", exc_info=True)
            yield SpanHandle()
            return
        try:
            yield SpanHandle(span)
        except BaseException as exc:
            with contextlib.suppress(Exception):
                span.update(level="ERROR", status_message=f"{type(exc).__name__}")
            with contextlib.suppress(Exception):
                manager.__exit__(type(exc), exc, exc.__traceback__)
            raise
        else:
            with contextlib.suppress(Exception):
                manager.__exit__(None, None, None)

    def generation(self, *, name: str, model: str, latency_s: float, usage: dict | None = None, error: str | None = None,
                   **metadata: Any) -> None:
        try:
            with self.client.start_as_current_generation(
                name=name, model=model, metadata=self._meta(None, {**metadata, "latency_ms": round(latency_s * 1000, 1)}),
                usage_details=usage or None, level="ERROR" if error else "DEFAULT", status_message=error,
            ):
                pass
        except Exception:  # noqa: BLE001
            log.debug("langfuse generation failed", exc_info=True)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self.client.flush()


_NOOP = Tracer()
_tracer: Tracer = _NOOP


def configure(settings) -> Tracer:
    """Called once at startup. Never raises: a misconfigured LangFuse degrades to the no-op tracer."""
    global _tracer
    if not settings.langfuse_enabled:
        _tracer = _NOOP
        return _tracer
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.warning("LANGFUSE_ENABLED=true but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are missing; tracing disabled")
        _tracer = _NOOP
        return _tracer
    try:
        _tracer = LangfuseTracer(settings.langfuse_public_key, settings.langfuse_secret_key, settings.langfuse_host)
        log.info("langfuse tracing enabled", extra={"host": settings.langfuse_host})
    except Exception:  # noqa: BLE001
        log.warning("langfuse could not be initialised; tracing disabled", exc_info=True)
        _tracer = _NOOP
    return _tracer


def get_tracer() -> Tracer:
    return _tracer


def reset_for_tests() -> None:
    global _tracer
    _tracer = _NOOP


def timed() -> float:
    return time.perf_counter()


# ---- convenience used by the pipeline (always safe to call, traced or not) ---------------------------------
_current_span: contextvars.ContextVar[SpanHandle | None] = contextvars.ContextVar("current_span", default=None)


@contextlib.contextmanager
def traced_span(name: str, *, invoice_id: str | None = None, **metadata: Any) -> Iterator[SpanHandle]:
    """Open a span on the configured tracer and make it the 'current' one for record_score()/annotate()."""
    with get_tracer().span(name, invoice_id=invoice_id, **metadata) as handle:
        token = _current_span.set(handle)
        try:
            yield handle
        finally:
            _current_span.reset(token)


def record_score(name: str, value: float | None) -> None:
    """Attach a numeric score (translation/OCR confidence, RAGAS metric, ...) to the current trace."""
    handle = _current_span.get()
    if handle is not None and value is not None:
        handle.score(name, value)


def annotate(**fields: Any) -> None:
    handle = _current_span.get()
    if handle is not None:
        handle.update(metadata=fields)
