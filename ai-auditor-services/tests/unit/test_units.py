import io
import zipfile
from pathlib import Path

import pytest

from app.core.errors import AppError
from app.repositories.erp_repo import similarity
from app.services import feedback_service as fs
from app.services import report_service, upload_service


def test_sanitise_filename():
    assert upload_service.sanitise_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert upload_service.sanitise_filename("C:\\x\\My Invoice (1).PDF") == "My_Invoice_1.pdf"
    assert upload_service.sanitise_filename("") == "invoice"
    assert len(upload_service.sanitise_filename("a" * 500 + ".pdf")) <= 120


def _docx_bytes(valid=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml" if valid else "other.xml", "<x/>")
    return buf.getvalue()


@pytest.mark.parametrize("ext,data,ok", [
    (".pdf", b"%PDF-1.7 xx", True), (".pdf", b"hello", False),
    (".png", b"\x89PNG\r\n\x1a\nxxxx", True), (".png", b"%PDF", False),
    (".jpg", b"\xff\xd8\xff\xe0xx", True), (".jpeg", b"\xff\xd8\xff\xdbxx", True),
    (".tif", b"II*\x00xxxx", True), (".docx", _docx_bytes(), True),
    (".docx", _docx_bytes(False), False), (".docx", b"PKnotzip", False),
])
def test_sniff(tmp_path, ext, data, ok):
    p = tmp_path / "f"
    p.write_bytes(data)
    assert upload_service.sniff_ok(ext, p) is ok


def test_stream_to_temp_limit(tmp_path):
    with pytest.raises(upload_service.TooLarge):
        upload_service.stream_to_temp(io.BytesIO(b"x" * 2000), tmp_path / ".tmp", 1000)
    assert list((tmp_path / ".tmp").iterdir()) == []  # temp file removed
    p, digest, size = upload_service.stream_to_temp(io.BytesIO(b"abc"), tmp_path / ".tmp", 1000)
    assert size == 3 and len(digest) == 64 and p.exists()


def test_derive_status():
    ev = lambda stage, state, **d: {"stage": stage, "state": state, "detail": d or None, "created_at": None}  # noqa: E731
    assert upload_service.derive_status([ev("QUEUED", "OK")], None)["status"] == "QUEUED"
    assert upload_service.derive_status([ev("QUEUED", "OK"), ev("EXTRACT", "STARTED")], None)["status"] == "PROCESSING"
    failed = upload_service.derive_status([ev("QUEUED", "OK"), ev("EXTRACT", "FAILED", error="no text")], None)
    assert failed["status"] == "FAILED" and failed["error"] == "no text"
    done = upload_service.derive_status([ev("COMPLETED", "OK"), ev("QUEUED", "DUPLICATE")], "id")
    assert done["status"] == "COMPLETED"


def test_parse_field():
    assert fs.parse_field("total_amount").kind == "money"
    s = fs.parse_field("line:2:quantity")
    assert (s.line_number, s.column) == (2, "quantity")
    for bad in ("recommendation", "line:0:quantity", "line:1:evil; drop", "file_path"):
        with pytest.raises(AppError) as e:
            fs.parse_field(bad)
        assert e.value.status == 422


def test_canonicalise():
    c = fs.canonicalise
    assert c(fs.parse_field("total_amount"), " 1,234.5 ") == "1234.50"
    assert c(fs.parse_field("line:1:quantity"), "3") == "3.000"
    assert c(fs.parse_field("invoice_date"), "2025-03-14") == "2025-03-14"
    assert c(fs.parse_field("currency"), "usd") == "USD"
    for f, v in [("total_amount", "abc"), ("total_amount", "-1"), ("invoice_date", "14/03/2025"),
                 ("currency", "US"), ("invoice_number", "  "), ("invoice_number", "x" * 65), ("total_amount", "NaN")]:
        with pytest.raises(AppError):
            c(fs.parse_field(f), v)


def test_report_path_safe(tmp_path):
    import uuid

    inv = uuid.uuid4()
    (tmp_path / f"{inv}.html").write_text("x")
    assert report_service.resolve_report_path(tmp_path, inv, "html") == (tmp_path / f"{inv}.html").resolve()
    assert report_service.resolve_report_path(tmp_path, inv, "json") is None       # missing
    assert report_service.resolve_report_path(tmp_path, inv, "../../etc") is None  # bad format
    outside = tmp_path.parent / "secret.html"
    outside.write_text("s")
    link = tmp_path / f"{uuid.uuid4()}.html"
    link.symlink_to(outside)
    assert report_service.resolve_report_path(tmp_path, uuid.UUID(link.stem), "html") is None  # symlink escape


def test_similarity_ranking():
    assert similarity("BlueOcean Transport", "BlueOcean Transport Co.") > 0.8
    assert similarity("Hafen Logistik", "HafenLogistik GmbH") > 0.7
    assert similarity("BLUE LOGISTIC VIETNAM", "BlueOcean Transport Co.") < 0.6
    assert similarity("", "x") == 0.0


def test_path_type():
    assert isinstance(Path("."), Path)
