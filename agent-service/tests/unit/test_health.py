"""/health contract: dependency map, 503 when any required dependency is unavailable."""
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import Settings
from app.main import create_app

HEALTHY = {"postgres": None, "redis": None, "qdrant": None, "erp": None, "llm": None}


def _client(probe_result):
    ctx = MagicMock()
    ctx.settings = Settings()
    ctx.checkpointer = None
    app = create_app(ctx=ctx, start_monitor=False, probe=lambda _s: dict(probe_result))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_probe_cache():
    routes._READY_CACHE.update(at=0.0, status=None)
    yield
    routes._READY_CACHE.update(at=0.0, status=None)


def test_all_dependencies_healthy():
    with _client(HEALTHY) as c:
        r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "dependencies": {"postgres": "connected", "qdrant": "connected", "redis": "connected", "mock_erp": "reachable"}}
    assert r.headers["x-request-id"]


@pytest.mark.parametrize("failing,key", [("postgres", "postgres"), ("qdrant", "qdrant"), ("redis", "redis"), ("erp", "mock_erp")])
def test_each_dependency_failing_independently_returns_503(failing, key):
    probe = {**HEALTHY, failing: "ConnectError: refused"}
    with _client(probe) as c:
        r = c.get("/health")
    body = r.json()
    assert r.status_code == 503 and body["status"] == "unhealthy"
    assert body["dependencies"][key].startswith("unreachable: ConnectError")
    healthy_keys = {"postgres", "qdrant", "redis", "mock_erp"} - {key}
    assert all(body["dependencies"][k] in ("connected", "reachable") for k in healthy_keys), "only the failing dependency is reported down"


def test_ready_adds_llm_configuration_and_is_what_docker_probes():
    with _client({**HEALTHY, "llm": "AWS_ACCESS_KEY_ID is not configured"}) as c:
        health, ready = c.get("/health"), c.get("/ready")
    assert health.status_code == 200  # the four contract dependencies are fine
    assert ready.status_code == 503 and ready.json()["dependencies"]["llm"].startswith("AWS_ACCESS_KEY_ID")  # cannot process invoices
    dockerfile = (__import__("pathlib").Path(__file__).resolve().parents[2] / "Dockerfile").read_text()
    assert "/ready" in dockerfile
