"""Test setup. API tests run against the real Postgres from docker compose (localhost:5432)
using the services_rw role. Fixtures seed audit rows as the superuser and delete them afterwards."""

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
ROOT = SERVICE_DIR.parent


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


_svc_env = _read_env(SERVICE_DIR / ".env")
_root_env = _read_env(ROOT / ".env")
_TMP = Path(tempfile.mkdtemp(prefix="auditor-tests-"))
os.environ["DATABASE_URL"] = _svc_env.get("DATABASE_URL", "").replace("@postgres:", "@localhost:")
os.environ["INCOMING_DIR"] = str(_TMP / "incoming")
os.environ["REPORTS_DIR"] = str(_TMP / "reports")
os.environ["MAX_UPLOAD_MB"] = "1"
os.environ["DB_WAIT_SECONDS"] = "3"
(_TMP / "reports").mkdir(parents=True, exist_ok=True)


def _superuser_conninfo() -> str:
    return (f"host=localhost port=5432 dbname={_root_env.get('POSTGRES_DB', 'auditor')} "
            f"user={_root_env.get('POSTGRES_USER', 'postgres')} password={_root_env.get('POSTGRES_PASSWORD', '')}")


@pytest.fixture(scope="session")
def su():
    psycopg = pytest.importorskip("psycopg")
    try:
        conn = psycopg.connect(_superuser_conninfo(), autocommit=True, connect_timeout=3)
    except Exception as exc:  # DB not running -> skip API tests
        pytest.skip(f"Postgres not reachable on localhost:5432: {exc}")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def client(su):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def seeded(su):
    """One fully populated invoice (2 validation runs, line items, events, reports)."""
    content = b"%PDF-1.4 seeded-test-invoice " + uuid.uuid4().bytes
    checksum = hashlib.sha256(content).hexdigest()
    inv = uuid.uuid4()
    tag = "TEST-" + inv.hex[:8]
    cur = su.cursor()
    cur.execute(
        "INSERT INTO audit.invoice_audit (invoice_id, file_path, file_checksum, extraction_method, raw_extracted_text, "
        "translated_text, source_language, translation_confidence, invoice_number, vendor_name, vendor_id, po_number, "
        "invoice_date, currency, total_amount, validation_status, recommendation, validation_run, extracted_raw, report_path) "
        "VALUES (%s,%s,%s,'pdfplumber','raw text','translated text','es',0.912,%s,'TEST Transporte Ibérico','VEND-003','PO-1003',"
        "'2025-03-14','EUR',380.00,'failed','review',2,'{\"tax_id\":\"X1\"}',%s)",
        (inv, f"/data/incoming/{checksum[:12]}_t.pdf", checksum, tag, f"{inv}.html"),
    )
    cur.execute(
        "INSERT INTO audit.invoice_line_items (invoice_id, line_number, item_code, description, quantity, unit_price, line_total) "
        "VALUES (%s,1,'SKU-201','Cajas',100,2.5,250.00),(%s,2,'SKU-202','Etiquetas',300,0.4,120.00)", (inv, inv))
    cur.execute(
        "INSERT INTO audit.discrepancies (invoice_id, validation_run, superseded, field_name, invoice_value, erp_value, "
        "deviation_pct, severity) VALUES (%s,1,true,'unit_price','3.0','2.5',20,'high'),"
        "(%s,2,false,'unit_price','0.4','0.3',33.3333,'high')", (inv, inv))
    cur.execute(
        "INSERT INTO audit.processing_events (invoice_id, file_checksum, stage, state, detail) VALUES "
        "(%s,%s,'DETECTED','OK',NULL),(%s,%s,'COMPLETED','OK',NULL)", (inv, checksum, inv, checksum))
    (Path(os.environ["REPORTS_DIR"]) / f"{inv}.html").write_text("<html>report</html>")
    (Path(os.environ["REPORTS_DIR"]) / f"{inv}.json").write_text('{"ok": true}')
    yield {"invoice_id": inv, "checksum": checksum, "content": content, "tag": tag}
    cur.execute("DELETE FROM audit.human_feedback WHERE invoice_id=%s", (inv,))
    cur.execute("DELETE FROM audit.discrepancies WHERE invoice_id=%s", (inv,))
    cur.execute("DELETE FROM audit.invoice_line_items WHERE invoice_id=%s", (inv,))
    cur.execute("DELETE FROM audit.processing_events WHERE invoice_id=%s OR file_checksum=%s", (inv, checksum))
    cur.execute("DELETE FROM audit.invoice_audit WHERE invoice_id=%s", (inv,))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_upload_events(request):
    """Upload tests name files TEST_*; remove the events they create (and any leftovers)."""
    yield
    try:
        import psycopg

        with psycopg.connect(_superuser_conninfo(), autocommit=True, connect_timeout=3) as c:
            c.execute("DELETE FROM audit.processing_events WHERE detail->>'original_filename' LIKE 'TEST\\_%'")
    except Exception:
        pass
