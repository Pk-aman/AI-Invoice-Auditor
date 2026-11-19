"""Migration 001 on a database created by the previous version (infra/postgres/migrate.py)."""
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from tests.conftest import ADMIN_URL

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[3]
LEGACY_SCHEMA = (Path(__file__).resolve().parents[1] / "fixtures" / "legacy_03_audit_schema.sql").read_text()


def _migrate_module():
    spec = importlib.util.spec_from_file_location("pg_migrate", ROOT / "infra" / "postgres" / "migrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def legacy_db(monkeypatch):
    """A scratch database in the OLD shape (schema + roles exist cluster-wide from the init scripts)."""
    name = "legacy_" + uuid.uuid4().hex[:8]
    base = ADMIN_URL.rsplit("/", 1)[0]
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = f"{base}/{name}"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("CREATE SCHEMA erp; CREATE SCHEMA audit; CREATE SCHEMA langgraph")
        c.execute(LEGACY_SCHEMA)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", url)
    yield url
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def _insert(url, invoice_id, checksum, translated, recommendation="approve"):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO audit.invoice_audit (invoice_id, file_checksum, translated_text, raw_extracted_text, validation_status,"
                  " recommendation) VALUES (%s,%s,%s,'raw','passed',%s)", (invoice_id, checksum, translated, recommendation))


def test_migration_backfills_exact_content_checksums_and_is_rerunnable(legacy_db):
    from app.core.normalize import content_checksum

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    _insert(legacy_db, a, "f1", "Hello   WORLD\n")
    _insert(legacy_db, b, "f2", "Another invoice")
    with psycopg.connect(legacy_db, autocommit=True) as c:
        c.execute("INSERT INTO audit.rag_evaluations (id, question, answer, route, relevance, groundedness, context_relevance)"
                  " VALUES (gen_random_uuid(),'q','a','open',.9,.8,.7)")
    mod = _migrate_module()
    assert mod.main() == 0 and mod.main() == 0  # second run: already applied
    with psycopg.connect(legacy_db) as c:
        rows = dict(c.execute("SELECT invoice_id::text, content_checksum FROM audit.invoice_audit").fetchall())
        assert rows[a] == content_checksum("hello world") == mod.content_checksum("Hello   WORLD\n")  # SAME normalisation as the agent
        assert rows[b] == content_checksum("Another invoice")
        status = c.execute("SELECT DISTINCT status, finalized_at IS NOT NULL FROM audit.invoice_audit").fetchall()
        assert status == [("LEGACY_FINALIZED", True)]
        assert c.execute("SELECT state, answer_relevance, faithfulness FROM audit.rag_evaluations").fetchone() == ("done", Decimal("0.900"), Decimal("0.800"))
        assert c.execute("SELECT count(*) FROM audit.schema_migrations").fetchone()[0] == 1
        with pytest.raises(psycopg.errors.NotNullViolation):
            c.execute("INSERT INTO audit.invoice_audit (invoice_id, file_checksum) VALUES (gen_random_uuid(), 'x')")


def test_migration_aborts_without_changing_anything_when_text_is_missing(legacy_db):
    with psycopg.connect(legacy_db, autocommit=True) as c:
        c.execute("INSERT INTO audit.invoice_audit (invoice_id, file_checksum) VALUES (gen_random_uuid(), 'nofile')")
    with pytest.raises(SystemExit, match="no translated_text"):
        _migrate_module().main()
    with psycopg.connect(legacy_db) as c:
        cols = c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='audit' AND table_name='invoice_audit'"
                         " AND column_name='content_checksum'").fetchall()
        assert cols == [], "the failed migration must roll back completely"
        assert c.execute("SELECT count(*) FROM audit.schema_migrations").fetchone()[0] == 0


def test_migration_aborts_on_equivalent_existing_invoices(legacy_db):
    _insert(legacy_db, str(uuid.uuid4()), "f1", "Same text")
    _insert(legacy_db, str(uuid.uuid4()), "f2", "same   TEXT")
    with pytest.raises(SystemExit, match="equivalent translated content"):
        _migrate_module().main()


def test_migrated_schema_matches_a_fresh_install(legacy_db, admin_query):
    _migrate_module().main()
    sql = ("SELECT table_name||'.'||column_name||':'||data_type||':'||is_nullable AS c FROM information_schema.columns "
           "WHERE table_schema='audit' ORDER BY 1")
    with psycopg.connect(legacy_db) as c:
        migrated = [r[0] for r in c.execute(sql).fetchall()]
    fresh = [r["c"] for r in admin_query(sql)]
    assert migrated == fresh
