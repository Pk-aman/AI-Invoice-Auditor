"""Raw-file checksum and post-translation content-checksum de-duplication."""
import shutil

import pytest

from app.core.normalize import content_checksum, normalize_content
from app.persistence.audit_repo import DuplicateInvoice
from app.schemas.lifecycle import Ingestion
from app.services.pipeline import Pipeline
from tests.conftest import INVOICE_TEXT, FakeLLM, make_docx, make_pdf

pytestmark = pytest.mark.db


def _events(admin_query):
    return [(e["stage"], e["state"], e["detail"]) for e in
            admin_query("SELECT stage, state, detail FROM audit.processing_events ORDER BY id")]


def _rows(admin_query):
    return admin_query("SELECT invoice_id::text AS id FROM audit.invoice_audit")


def test_same_file_twice_is_rejected_by_raw_checksum(make_ctx, tmp_path, admin_query):
    ctx = make_ctx()
    pipe = Pipeline(ctx)
    first = make_docx(tmp_path / "one.docx")
    pipe.process_path(first)
    llm_calls = len(ctx.llm.calls)
    second = tmp_path / "renamed_copy.docx"
    shutil.copy(first, second)  # same bytes, different filename
    out = pipe.process_path(second)
    assert out.get("duplicate") is True
    assert len(_rows(admin_query)) == 1
    dup = [e for e in _events(admin_query) if e[1] == "DUPLICATE"]
    assert len(dup) == 1 and dup[0][0] == "DETECTED" and dup[0][2]["kind"] == "file"
    assert dup[0][2]["existing_invoice_id"] == _rows(admin_query)[0]["id"]
    assert len(ctx.llm.calls) == llm_calls, "no parsing / translation / validation may run for a duplicate"


def test_equivalent_content_in_a_different_file_is_rejected_after_translation(make_ctx, tmp_path, admin_query):
    ctx = make_ctx()
    pipe = Pipeline(ctx)
    pipe.process_path(make_docx(tmp_path / "a.docx"))
    llm_calls = list(ctx.llm.calls)
    b = make_docx(tmp_path / "b.docx", INVOICE_TEXT.upper().replace("\n", "\n\n   "))  # other case + whitespace, other bytes
    out = pipe.process_path(b)
    assert out.get("duplicate") is True and len(_rows(admin_query)) == 1
    dup = [e for e in _events(admin_query) if e[1] == "DUPLICATE"]
    assert dup[0][0] == "DEDUPE" and dup[0][2]["kind"] == "content"
    assert dup[0][2]["existing_invoice_id"] == _rows(admin_query)[0]["id"]
    assert ctx.llm.calls == llm_calls, "the duplicate must stop before parsing/validation/reporting"
    stages = [e[0] for e in _events(admin_query)]
    assert stages.count("INGEST") == 2 and stages.count("PARSE") == 2  # first run only (STARTED+OK)


def test_docx_and_pdf_copies_with_equivalent_translated_content(make_ctx, tmp_path, admin_query):
    ctx = make_ctx()
    pipe = Pipeline(ctx)
    pipe.process_path(make_docx(tmp_path / "invoice.docx"))
    out = pipe.process_path(make_pdf(tmp_path / "invoice.pdf"))
    assert out.get("duplicate") is True, "cross-format copy must be caught by the content checksum"
    assert len(_rows(admin_query)) == 1


def test_different_invoices_are_both_ingested(make_ctx, tmp_path, admin_query):
    ctx = make_ctx()
    pipe = Pipeline(ctx)
    pipe.process_path(make_docx(tmp_path / "a.docx"))
    other = INVOICE_TEXT.replace("INV-1001", "INV-2002").replace("960.00", "961.00")
    out = pipe.process_path(make_docx(tmp_path / "b.docx", other))
    assert not out.get("duplicate") and len(_rows(admin_query)) == 2
    checksums = admin_query("SELECT content_checksum FROM audit.invoice_audit")
    assert len({c["content_checksum"] for c in checksums}) == 2


def test_race_between_twins_is_arbitrated_by_the_unique_constraints(make_ctx, admin_query):
    ctx = make_ctx()

    def ing(i, fchk, cchk):
        return Ingestion(invoice_id=i, file_path="/x", file_checksum=fchk, content_checksum=cchk, extraction_method="docx",
                         raw_extracted_text="r", translated_text="t", source_language="en", translation_confidence=0.99)

    a = "11111111-1111-1111-1111-111111111111"
    ctx.repo.ingest_translation(ing(a, "f1", "c1"))
    with pytest.raises(DuplicateInvoice) as e1:
        ctx.repo.ingest_translation(ing("22222222-2222-2222-2222-222222222222", "f2", "c1"))
    assert e1.value.kind == "content" and e1.value.existing_invoice_id == a
    with pytest.raises(DuplicateInvoice) as e2:
        ctx.repo.ingest_translation(ing("33333333-3333-3333-3333-333333333333", "f1", "c3"))
    assert e2.value.kind == "file" and e2.value.existing_invoice_id == a
    assert len(_rows(admin_query)) == 1


def test_failed_pipeline_before_ingest_persists_nothing(make_ctx, tmp_path, admin_query):
    ctx = make_ctx(llm=FakeLLM({}, fail_roles={"translation"}))
    pipe = Pipeline(ctx)
    spanish = "Factura número 123\nFecha de factura: 14 de marzo de 2025\nProveedor: Transporte Ibérico S.A.\nTotal: 1.250,00 EUR\n" * 2
    out = pipe.process_path(make_docx(tmp_path / "es.docx", spanish))
    assert out.get("failed") is True and _rows(admin_query) == []
    assert any(e[0] == "TRANSLATE" and e[1] == "FAILED" for e in _events(admin_query))


@pytest.mark.parametrize("a,b,same", [
    ("Hello   WORLD", "hello world", True),
    ("Hello\tWorld\n", "  hello  world ", True),
    ("Invoice 1001", "Invoice 1002", False),
])
def test_content_checksum_normalisation(a, b, same):
    assert normalize_content("Hello   WORLD") == "hello world"
    assert (content_checksum(a) == content_checksum(b)) is same
