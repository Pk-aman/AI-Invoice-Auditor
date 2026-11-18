import hashlib
import io
import os
from pathlib import Path

import pytest

PDF = b"%PDF-1.4 " + b"A" * 200


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200 and r.json()["database"] == "ok"


# ---------------- ERP (brief's contract) ----------------
def test_po(client):
    r = client.get("/po/PO-1001")
    assert r.status_code == 200
    body = r.json()
    assert body["vendor_id"] == "VEND-001" and len(body["line_items"]) == 3
    first = body["line_items"][0]
    assert first == {"line_number": 1, "item_code": "SKU-001", "description": "Pallet Wrapping Film",
                     "qty": 50.0, "unit_price": 12.0, "currency": "USD"}


def test_vendor_sku_and_404s(client):
    assert client.get("/vendor/VEND-003").json()["vendor_name"] == "Transporte Ibérico S.A."
    assert client.get("/sku/SKU-001").json() == {"item_code": "SKU-001", "category": "Packaging", "uom": "roll", "gst_rate": 10.0}
    for url in ("/po/PO-9999", "/vendor/NOPE", "/sku/NOPE"):
        r = client.get(url)
        assert r.status_code == 404 and r.json()["error"]["code"].endswith("_not_found")


def test_vendor_search_and_pos(client):
    r = client.get("/api/v1/erp/vendors/search", params={"name": "BlueOcean Transport"}).json()
    assert r[0]["vendor_id"] == "VEND-002" and r[0]["score"] > 0.8
    assert client.get("/api/v1/erp/vendors/search", params={"name": "Hafen Logistik"}).json()[0]["vendor_id"] == "VEND-004"
    # the supplied sample's vendor is not in the ERP
    assert all(m["score"] < 0.6 for m in client.get(
        "/api/v1/erp/vendors/search", params={"name": "BLUE LOGISTIC VIETNAM"}).json())
    assert client.get("/api/v1/erp/vendors/search").status_code == 422
    pos = client.get("/api/v1/erp/pos", params={"vendor_id": "VEND-004"}).json()
    assert [p["po_number"] for p in pos] == ["PO-1004"] and len(pos[0]["line_items"]) == 2


# ---------------- uploads ----------------
def _post(client, name, data):
    return client.post("/api/v1/uploads", files=[("files", (name, io.BytesIO(data), "application/octet-stream"))])


def test_upload_queue_duplicate_and_status(client, su):
    data = PDF + os.urandom(16)
    r = _post(client, "TEST_a.pdf", data)
    assert r.status_code == 202
    res = r.json()[0]
    checksum = hashlib.sha256(data).hexdigest()
    assert res["status"] == "QUEUED" and res["checksum"] == checksum
    stored = Path(os.environ["INCOMING_DIR"]) / f"{checksum[:12]}_TEST_a.pdf"
    assert stored.read_bytes() == data
    assert not list((Path(os.environ["INCOMING_DIR"]) / ".tmp").iterdir())  # no temp leftovers

    st = client.get(f"/api/v1/uploads/{checksum}/status").json()
    assert st["status"] == "QUEUED" and st["invoice_id"] is None

    again = _post(client, "TEST_renamed.pdf", data)  # same content, different name
    assert again.status_code == 200 and again.json()[0]["status"] == "DUPLICATE"
    assert len(list(Path(os.environ["INCOMING_DIR"]).glob(f"{checksum[:12]}_*"))) == 1
    su.execute("DELETE FROM audit.processing_events WHERE file_checksum=%s", (checksum,))


def test_upload_duplicate_of_audited_invoice(client, seeded):
    chk = seeded["checksum"]
    r = _post(client, "TEST_dup.pdf", seeded["content"]).json()[0]
    assert r["status"] == "DUPLICATE" and r["invoice_id"] == str(seeded["invoice_id"])
    st = client.get(f"/api/v1/uploads/{chk}/status").json()
    assert st["status"] == "COMPLETED" and st["invoice_id"] == str(seeded["invoice_id"])


def test_upload_rejections(client):
    assert _post(client, "TEST_x.exe", b"MZ").json()[0]["status"] == "REJECTED"
    assert _post(client, "TEST_fake.pdf", b"not a pdf at all").json()[0]["status"] == "REJECTED"
    assert _post(client, "TEST_empty.pdf", b"").json()[0]["status"] == "REJECTED"
    big = _post(client, "TEST_big.pdf", b"%PDF" + b"0" * (1024 * 1024 + 10)).json()[0]
    assert big["status"] == "REJECTED" and "larger" in big["reason"]
    assert client.get(f"/api/v1/uploads/{'0' * 64}/status").status_code == 404
    assert client.get("/api/v1/uploads/zzz/status").status_code == 422


# ---------------- invoices ----------------
def test_list_and_filters(client, seeded):
    tag = seeded["tag"]
    r = client.get("/api/v1/invoices", params={"q": tag}).json()
    assert r["total"] == 1 and r["items"][0]["invoice_id"] == str(seeded["invoice_id"])
    assert r["items"][0]["total_amount"] == 380.0
    assert client.get("/api/v1/invoices", params={"q": tag, "recommendation": "approve"}).json()["total"] == 0
    assert client.get("/api/v1/invoices", params={"q": tag, "currency": "eur", "status": "failed"}).json()["total"] == 1
    assert client.get("/api/v1/invoices", params={"q": tag, "vendor": "ibérico"}).json()["total"] == 1
    assert client.get("/api/v1/invoices", params={"q": tag, "date_from": "2999-01-01"}).json()["total"] == 0
    assert client.get("/api/v1/invoices", params={"page_size": 1, "sort": "total_amount", "order": "asc"}).status_code == 200
    assert client.get("/api/v1/invoices", params={"sort": "id; drop table x"}).status_code == 200  # whitelist fallback
    assert client.get("/api/v1/invoices", params={"page": 0}).status_code == 422
    assert client.get("/api/v1/invoices", params={"recommendation": "maybe"}).status_code == 422


def test_detail_children(client, seeded):
    base = f"/api/v1/invoices/{seeded['invoice_id']}"
    d = client.get(base).json()
    assert d["invoice_number"] == seeded["tag"] and d["report_available"] and d["extracted_raw"] == {"tax_id": "X1"}
    assert [x["item_code"] for x in client.get(f"{base}/line-items").json()] == ["SKU-201", "SKU-202"]
    latest = client.get(f"{base}/discrepancies").json()
    assert len(latest) == 1 and latest[0]["validation_run"] == 2 and latest[0]["superseded"] is False
    assert len(client.get(f"{base}/discrepancies", params={"run": "all"}).json()) == 2
    assert client.get(f"{base}/text", params={"kind": "raw"}).json()["text"] == "raw text"
    assert client.get(f"{base}/text").json()["text"] == "translated text"
    events = [e for e in client.get(f"{base}/events").json() if e["state"] != "DUPLICATE"]
    assert [e["stage"] for e in events] == ["DETECTED", "COMPLETED"]
    assert client.get("/api/v1/invoices/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/v1/invoices/not-a-uuid").status_code == 422


def test_reports(client, seeded):
    base = f"/api/v1/invoices/{seeded['invoice_id']}/report"
    h = client.get(base)
    assert h.status_code == 200 and h.text == "<html>report</html>" and h.headers["content-security-policy"] == "sandbox"
    assert client.get(base, params={"format": "json"}).json() == {"ok": True}
    assert client.get(base, params={"format": "../../etc/passwd"}).status_code == 422
    assert client.get("/api/v1/invoices/00000000-0000-0000-0000-000000000000/report").status_code == 404


# ---------------- feedback ----------------
def test_feedback_insert_only_and_effective_value(client, seeded, su):
    inv = seeded["invoice_id"]
    base = f"/api/v1/invoices/{inv}/feedback"
    r = client.post(base, json={"field_name": "total_amount", "corrected_value": "375.5", "corrected_by": "auditor1"})
    assert r.status_code == 201
    first = r.json()
    assert first["original_value"] == "380.00" and first["corrected_value"] == "375.50" and first["revalidated"] is False
    second = client.post(base, json={"field_name": "total_amount", "corrected_value": "370", "corrected_by": "auditor1"}).json()
    assert second["original_value"] == "375.50"  # latest correction is the effective value
    line = client.post(base, json={"field_name": "line:2:unit_price", "corrected_value": "0.3", "corrected_by": "a"}).json()
    assert line["original_value"] == "0.4000" and line["corrected_value"] == "0.3000"

    hist = client.get(base).json()
    assert [h["id"] for h in hist] == sorted([h["id"] for h in hist], reverse=True) and len(hist) == 3
    cur = su.execute("SELECT total_amount FROM audit.invoice_audit WHERE invoice_id=%s", (inv,))
    assert str(cur.fetchone()[0]) == "380.00"  # original extraction never overwritten

    d = client.get(f"/api/v1/invoices/{inv}").json()
    assert d["pending_corrections"] == 3 and d["corrections"]["total_amount"] == "370.00"


def test_feedback_validation(client, seeded):
    base = f"/api/v1/invoices/{seeded['invoice_id']}/feedback"
    ok = {"corrected_value": "1", "corrected_by": "a"}
    assert client.post(base, json={"field_name": "recommendation", **ok}).status_code == 422
    assert client.post(base, json={"field_name": "total_amount", "corrected_value": "abc", "corrected_by": "a"}).status_code == 422
    assert client.post(base, json={"field_name": "line:9:quantity", **ok}).status_code == 404
    assert client.post(base, json={"field_name": "total_amount", "corrected_value": "1"}).status_code == 422
    missing = "/api/v1/invoices/00000000-0000-0000-0000-000000000000/feedback"
    assert client.post(missing, json={"field_name": "total_amount", **ok}).status_code == 404


# ---------------- stats ----------------
def test_stats(client, seeded):
    s = client.get("/api/v1/stats/summary").json()
    assert s["total_invoices"] >= 1 and s["needs_review"] >= 1 and "avg_translation_confidence" in s
    assert any(v["vendor_name"] for v in client.get("/api/v1/stats/by-vendor").json())
    assert isinstance(client.get("/api/v1/stats/by-day", params={"days": 7}).json(), list)
    assert any(d["field_name"] == "unit_price" for d in client.get("/api/v1/stats/discrepancy-types").json())
    assert any(c["currency"] == "EUR" for c in client.get("/api/v1/stats/currencies").json())


def test_error_envelope(client):
    r = client.get("/nope")
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message", "detail"}


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_openapi(client, path):
    assert client.get(path).status_code == 200
