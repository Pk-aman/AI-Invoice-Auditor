"""Shared fixtures. DB tests need a PostgreSQL initialised from infra/postgres/init (see tests/README.md):

    TEST_ADMIN_DATABASE_URL=postgresql://postgres:<pw>@localhost:55432/auditor   (truncates tables between tests)
    TEST_DATABASE_URL=postgresql://agent_rw:<pw>@localhost:55432/auditor          (the role the service really uses)

Without them the `db` tests are skipped, the unit tests still run. No test ever calls a real LLM, AWS, or the ERP.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.clients.erp_client import ErpClient
from app.core.config import Settings
from app.core.context import AppContext
from app.core.rules import parse_rules
from app.schemas.models import ErpPoLine, ErpPurchaseOrder, ErpSku, ErpVendor

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")
AGENT_URL = os.environ.get("TEST_DATABASE_URL")
RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "rules.yaml"
EMBED_DIM = 8


def pytest_collection_modifyitems(config, items):
    if ADMIN_URL and AGENT_URL:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL / TEST_ADMIN_DATABASE_URL not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


# ---- fakes ------------------------------------------------------------------------------------
class FakeLLM:
    """Deterministic stand-in for the LLM gateway. `parsed` is what the parser agent 'extracts'."""

    models: dict = {}

    def __init__(self, parsed: dict | None = None, fail_roles: set[str] | None = None):
        self.parsed = parsed or {}
        self.calls: list[str] = []
        self.fail_roles = fail_roles or set()

    def complete(self, role, system, user, *, json_mode=False, max_tokens=None):
        self.calls.append(role)
        if role in self.fail_roles:
            from app.core.llm import LLMError

            raise LLMError(f"{role} is down")
        if "extract structured data" in system:
            return json.dumps(self.parsed)
        if "professional translator" in system:
            m = re.search(r"Text:\n(.*)", user, re.S)
            return (m.group(1) if m else user).strip()
        return "A short plain-English summary."

    def embed(self, texts):
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode()).digest()
            out.append([b / 255 for b in digest[:EMBED_DIM]])
        return out


class FakeErp(ErpClient):
    def __init__(self, down: bool = False):
        self.down = down
        self.vendors = {"VEND-001": ErpVendor(vendor_id="VEND-001", vendor_name="Global Logistics Ltd", country="UK", currency="USD")}
        self.pos = {"PO-1001": ErpPurchaseOrder(po_number="PO-1001", vendor_id="VEND-001", line_items=[
            ErpPoLine(line_number=1, item_code="SKU-001", description="Pallet Wrapping Film", qty=Decimal(50), unit_price=Decimal("12.00"), currency="USD"),
            ErpPoLine(line_number=2, item_code="SKU-002", description="Industrial Gloves", qty=Decimal(120), unit_price=Decimal("3.00"), currency="USD"),
        ])}
        self.skus = {"SKU-001": ErpSku(item_code="SKU-001", category="Packaging", uom="roll", gst_rate=Decimal(10)),
                     "SKU-002": ErpSku(item_code="SKU-002", category="Safety", uom="pair", gst_rate=Decimal(10))}

    def _check(self):
        if self.down:
            from app.clients.erp_client import ErpUnavailable

            raise ErpUnavailable("erp down")

    def get_vendor(self, vendor_id):
        self._check()
        return self.vendors.get(vendor_id)

    def get_po(self, po_number):
        self._check()
        return self.pos.get(po_number)

    def get_sku(self, sku_code):
        self._check()
        return self.skus.get(sku_code)

    def search_vendors(self, name, limit=5):
        self._check()
        return [v.model_copy(update={"score": 0.9}) for v in self.vendors.values() if name.lower() in v.vendor_name.lower()]

    def list_pos(self, vendor_id):
        self._check()
        return [p for p in self.pos.values() if p.vendor_id == vendor_id]

    def health(self):
        return not self.down


GOOD_INVOICE = {
    "invoice_number": "INV-1001", "vendor_name": "Global Logistics Ltd", "po_number": "PO-1001", "invoice_date": "2025-03-14",
    "currency": "USD", "subtotal": 960, "tax_amount": 0, "total_amount": 960,
    "line_items": [
        {"item_code": "SKU-001", "description": "Pallet Wrapping Film", "quantity": 50, "unit_price": 12.00, "line_total": 600},
        {"item_code": "SKU-002", "description": "Industrial Gloves", "quantity": 120, "unit_price": 3.00, "line_total": 360},
    ],
}
INVOICE_TEXT = ("Global Logistics Ltd\nInvoice Number: INV-1001\nPO Number: PO-1001\nInvoice Date: 2025-03-14\nCurrency: USD\n"
                "SKU-001 Pallet Wrapping Film 50 12.00 600.00\nSKU-002 Industrial Gloves 120 3.00 360.00\nTotal: 960.00\n")


def make_docx(path: Path, text: str = INVOICE_TEXT) -> Path:
    import docx

    d = docx.Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    d.save(str(path))
    return path


def make_pdf(path: Path, text: str = INVOICE_TEXT) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 72), text, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def invoice_text():
    return INVOICE_TEXT


# ---- database ------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def pg_pools():
    if not (ADMIN_URL and AGENT_URL):
        pytest.skip("no test database")
    from app.core.db import make_pool

    agent = make_pool(AGENT_URL)
    cp = make_pool(AGENT_URL, max_size=4, search_path="langgraph")
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(cp)
    saver.setup()
    admin = make_pool(ADMIN_URL, max_size=2)
    yield agent, saver, admin
    for p in (agent, cp, admin):
        p.close()


@pytest.fixture
def clean_db(pg_pools):
    """Empty audit/checkpoint tables before each test (admin connection; the app role has no TRUNCATE)."""
    _, _, admin = pg_pools
    with admin.connection() as c:
        c.execute("TRUNCATE audit.human_feedback, audit.review_decisions, audit.validation_reports, audit.discrepancies,"
                  " audit.invoice_line_items, audit.processing_events, audit.rag_evaluations, audit.invoice_audit CASCADE")
        for t in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            c.execute(f"TRUNCATE langgraph.{t}")  # noqa: S608
    return admin


@pytest.fixture
def admin_query(pg_pools):
    _, _, admin = pg_pools

    def run(sql, params=()):
        with admin.connection() as c:
            return c.execute(sql, params).fetchall()

    return run


@pytest.fixture
def settings(tmp_path):
    incoming, reports = tmp_path / "incoming", tmp_path / "reports"
    incoming.mkdir()
    return Settings(incoming_dir=incoming, reports_dir=reports, rules_path=RULES_PATH, embedding_dim=EMBED_DIM,
                    database_url=AGENT_URL or "postgresql://x:y@localhost/z", rag_reflection="off")


@pytest.fixture
def make_ctx(pg_pools, clean_db, settings):
    """Factory so a test can choose its LLM/ERP doubles."""
    import fakeredis
    from qdrant_client import QdrantClient

    from app.clients.qdrant_client import ensure_collection
    from app.persistence.audit_repo import AuditRepo

    pool, saver, _ = pg_pools
    rules = parse_rules(__import__("yaml").safe_load(RULES_PATH.read_text()))

    def factory(llm: FakeLLM | None = None, erp: FakeErp | None = None) -> AppContext:
        qdrant = QdrantClient(":memory:")
        ensure_collection(qdrant, settings.qdrant_collection, EMBED_DIM)
        return AppContext(settings=settings, rules=rules, repo=AuditRepo(pool), llm=llm or FakeLLM(GOOD_INVOICE), erp=erp or FakeErp(),
                          qdrant=qdrant, redis=fakeredis.FakeRedis(), pool=pool, checkpointer=saver)

    return factory


@pytest.fixture
def uid():
    return str(uuid.uuid4())


@pytest.fixture
def write_log(pg_pools, clean_db):
    """Statement-level trigger log of every INSERT/UPDATE on the business tables (proves 'exactly two writes')."""
    _, _, admin = pg_pools
    tables = ("invoice_audit", "invoice_line_items", "discrepancies", "validation_reports")
    with admin.connection() as c:
        c.execute("DROP TABLE IF EXISTS audit.test_write_log")
        c.execute("CREATE TABLE audit.test_write_log (id bigserial primary key, tbl text, op text, txid bigint)")
        c.execute("CREATE OR REPLACE FUNCTION audit.test_log_write() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$"
                  " BEGIN INSERT INTO audit.test_write_log(tbl, op, txid) VALUES (TG_TABLE_NAME, TG_OP, txid_current()); RETURN NULL; END $$")
        for t in tables:
            c.execute(f"CREATE TRIGGER trg_test_log AFTER INSERT OR UPDATE ON audit.{t} FOR EACH STATEMENT EXECUTE FUNCTION audit.test_log_write()")  # noqa: S608

    def rows():
        with admin.connection() as c:
            return [(r["tbl"], r["op"], r["txid"]) for r in c.execute("SELECT tbl, op, txid FROM audit.test_write_log ORDER BY id").fetchall()]

    yield rows
    with admin.connection() as c:
        for t in tables:
            c.execute(f"DROP TRIGGER IF EXISTS trg_test_log ON audit.{t}")  # noqa: S608
        c.execute("DROP TABLE IF EXISTS audit.test_write_log")
        c.execute("DROP FUNCTION IF EXISTS audit.test_log_write()")
