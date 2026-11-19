"""LangFuse is optional: disabled by default, never breaks business logic, never leaks content."""
import pytest

from app.core import observability as obs
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _reset():
    obs.reset_for_tests()
    yield
    obs.reset_for_tests()


def test_disabled_by_default_is_a_noop():
    t = obs.configure(Settings())
    assert t.enabled is False and obs.get_tracer() is t
    with obs.traced_span("x", invoice_id="i") as h:
        h.update(a=1)
        h.score("s", 1.0)
    obs.record_score("s", 0.5)
    obs.annotate(a=1)
    t.generation(name="n", model="m", latency_s=0.1)


def test_enabled_without_keys_degrades_to_noop():
    assert obs.configure(Settings(langfuse_enabled=True)).enabled is False


def test_misconfigured_langfuse_never_breaks_startup(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bad sdk")

    monkeypatch.setattr(obs, "LangfuseTracer", boom)
    assert obs.configure(Settings(langfuse_enabled=True, langfuse_public_key="pk", langfuse_secret_key="sk")).enabled is False


class _FakeCtx:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        self.log.append("enter")
        return _FakeSpan(self.log)

    def __exit__(self, *exc):
        self.log.append(("exit", exc[0].__name__ if exc[0] else None))


class _FakeSpan:
    def __init__(self, log):
        self.log = log

    def update(self, **f):
        self.log.append(("update", f))

    def score_trace(self, **f):
        self.log.append(("score", f))


class _FakeClient:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def start_as_current_span(self, **kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.log.append(("span", kw))
        return _FakeCtx(self.log)

    def start_as_current_generation(self, **kw):
        if self.fail:
            raise RuntimeError("langfuse down")
        self.log.append(("generation", kw))
        return _FakeCtx(self.log)

    def flush(self):
        raise RuntimeError("flush failed")


def _tracer(log, fail=False):
    t = obs.LangfuseTracer.__new__(obs.LangfuseTracer)
    t.client = _FakeClient(log, fail)
    return t


def test_trace_id_is_deterministic_per_invoice_and_spans_correlate():
    log: list = []
    t = _tracer(log)
    with t.span("node.parse", invoice_id="inv-1", stage="PARSE") as h:
        h.score("translation_confidence", 0.97)
    (_, kw) = next(e for e in log if isinstance(e, tuple) and e[0] == "span")
    assert kw["trace_context"] == {"trace_id": obs.trace_id_for("inv-1")} and len(obs.trace_id_for("inv-1")) == 32
    assert obs.trace_id_for("inv-1") != obs.trace_id_for("inv-2")
    assert kw["metadata"]["invoice_id"] == "inv-1" and kw["metadata"]["stage"] == "PARSE"
    assert ("score", {"name": "translation_confidence", "value": 0.97}) in log


def test_sdk_failures_never_change_the_business_outcome():
    t = _tracer([], fail=True)
    ran = []
    with t.span("x", invoice_id="i"):
        ran.append(1)  # the traced body still runs
    t.generation(name="n", model="m", latency_s=1.0)  # no exception
    t.flush()
    assert ran == [1]


def test_exceptions_in_traced_code_propagate_and_mark_the_span():
    log: list = []
    t = _tracer(log)
    with pytest.raises(ValueError), t.span("x", invoice_id="i"):
        raise ValueError("business error")
    assert ("exit", "ValueError") in log and any(e[0] == "update" and e[1]["level"] == "ERROR" for e in log if isinstance(e, tuple))


def test_generation_records_sizes_and_never_content():
    log: list = []
    t = _tracer(log)
    t.generation(name="llm.reasoning", model="bedrock/amazon.nova-lite-v1:0", latency_s=0.5, usage={"input": 3, "output": 4}, prompt_chars=99)
    (_, kw) = next(e for e in log if isinstance(e, tuple) and e[0] == "generation")
    assert kw["model"].startswith("bedrock/") and kw["usage_details"] == {"input": 3, "output": 4}
    assert kw["metadata"]["prompt_chars"] == 99 and kw["metadata"]["latency_ms"] == 500.0
    assert not {"input", "output", "prompt", "completion"} & set(kw)  # no prompt/completion text is ever sent
