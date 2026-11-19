"""PDF extraction is PyMuPDF direct-first with per-page Tesseract fallback; the recorded method is accurate."""
import pymupdf
import pytest

from app.extraction import extract as ex
from app.extraction.extract import ExtractionError, extract, extract_pdf
from tests.conftest import INVOICE_TEXT, make_docx, make_pdf


def _pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((50, 72), text, fontsize=10)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def fake_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(ex, "tesseract_available", lambda: True)

    def ocr(img, langs, **kw):
        calls.append((img.size, langs))
        return "OCR TEXT FROM SCAN", 0.8

    monkeypatch.setattr(ex, "ocr_image", ocr)
    return calls


def test_text_pdf_is_read_directly_without_ocr(tmp_path, fake_ocr):
    res = extract(make_pdf(tmp_path / "a.pdf"), langs="eng")
    assert res.method == "pymupdf" and "INV-1001" in res.text and res.ocr_confidence is None and fake_ocr == []


def test_scanned_pdf_is_rendered_and_ocrd_per_page(tmp_path, fake_ocr):
    res = extract(_pdf(tmp_path / "scan.pdf", ["", ""]), langs="eng+spa+deu+fra")
    assert res.method == "tesseract" and res.text.count("OCR TEXT FROM SCAN") == 2 and res.ocr_confidence == pytest.approx(0.8)
    assert len(fake_ocr) == 2 and fake_ocr[0][1] == "eng+spa+deu+fra"


def test_mixed_pdf_ocrs_only_the_pages_that_need_it_and_keeps_page_order(tmp_path, fake_ocr):
    res = extract_pdf(_pdf(tmp_path / "mixed.pdf", [INVOICE_TEXT, "", "Second text page with enough characters to count"]), "eng")
    assert res.method == "pymupdf+tesseract" and len(fake_ocr) == 1
    first, middle, last = res.text.split("\n\n")
    assert "INV-1001" in first and middle == "OCR TEXT FROM SCAN" and "Second text page" in last


def test_docx_and_unsupported_and_corrupt(tmp_path):
    assert extract(make_docx(tmp_path / "a.docx"), langs="eng").method == "docx"
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError) as e:
        extract(bad, langs="eng")
    assert e.value.method == "failed"
    with pytest.raises(ExtractionError):
        extract(tmp_path / "x.txt", langs="eng")


def test_ocr_without_tesseract_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "tesseract_available", lambda: False)
    with pytest.raises(ExtractionError, match="Tesseract is not installed"):
        extract(_pdf(tmp_path / "scan.pdf", [""]), langs="eng")


def test_default_ocr_languages_include_the_required_packs():
    from app.core.config import Settings

    langs = set(Settings().ocr_langs.split("+"))
    assert {"eng", "spa", "deu", "fra"} <= langs and "vie" in langs  # vie kept: the Vietnamese sample still needs it
