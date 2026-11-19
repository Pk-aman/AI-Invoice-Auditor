
from tests.conftest import pytestmark_db

pytestmark = pytestmark_db


def test_health_contract_when_healthy(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "healthy", "dependencies": {"postgres": "connected", "incoming_dir": "writable"}}
    assert client.get("/ready").json() == r.json()


def test_postgres_down_returns_503(client, monkeypatch):
    from app.routers import health

    monkeypatch.setattr(health, "db_ok", lambda: (False, "OperationalError"))
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "unhealthy"
    assert r.json()["dependencies"] == {"postgres": "unreachable: OperationalError", "incoming_dir": "writable"}


def test_unwritable_incoming_volume_returns_503(client, monkeypatch):
    from app.routers import health

    monkeypatch.setattr(health, "_writable", lambda _p: False)
    r = client.get("/health")
    assert r.status_code == 503 and r.json()["dependencies"]["incoming_dir"] == "not writable"
    assert r.json()["dependencies"]["postgres"] == "connected"


def test_dockerfile_probes_the_health_endpoint():
    from pathlib import Path

    assert "/health" in (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
