"""ERP page: read-only view of vendors, purchase orders and SKUs."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import api_client

PAGE = Path(__file__).resolve().parents[1] / "app" / "pages" / "erp.py"
DATA = {
    "vendors": [{"vendor_id": "V1", "vendor_name": "Acme Ltd", "country": "IN", "currency": "INR"}],
    "skus": [{"item_code": "S1", "category": "Parts", "uom": "EA", "gst_rate": 18.0}],
    "purchase_orders": [{"po_number": "PO-1", "vendor_id": "V1", "line_items": [
        {"line_number": 1, "item_code": "S1", "description": "Bolt", "qty": 2.0, "unit_price": 10.0, "currency": "INR"}]}],
}


def test_erp_page_shows_counts_and_po_totals(monkeypatch):
    monkeypatch.setattr(api_client, "erp_overview", lambda: DATA)
    at = AppTest.from_file(str(PAGE), default_timeout=20).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric} == {"Vendors": "1", "Purchase orders": "1 (1 lines)", "SKUs": "1"}
    assert any("PO-1" in e.label and "20.00 INR" in e.label for e in at.expander)


def test_erp_page_reports_backend_failure(monkeypatch):
    def boom():
        raise api_client.ApiError("The service could not be reached.", "unreachable")
    monkeypatch.setattr(api_client, "erp_overview", boom)
    at = AppTest.from_file(str(PAGE), default_timeout=20).run()
    assert at.error and "could not be reached" in at.error[0].value
