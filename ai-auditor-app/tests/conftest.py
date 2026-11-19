import os

os.environ.setdefault("SERVICES_URL", "http://services.test")
os.environ.setdefault("AGENT_URL", "http://agent.test")

import pytest  # noqa: E402

from app import api_client  # noqa: E402

IID = "11111111-1111-1111-1111-111111111111"
REPORT = {"validation_run": 1, "recommendation": "review", "recommendation_label": "Needs manual review", "validation_status": "failed",
          "summary": "The unit price is above the ERP price.", "reasons": ["unit_price differs"], "missing_fields": [],
          "discrepancies": [{"field_name": "line[1].unit_price", "invoice_value": "14", "erp_value": "12", "deviation_pct": "16.6",
                             "severity": "medium"}], "data_findings": [], "corrections": {}}
INVOICE = {"invoice_number": "INV-1001", "vendor_name": "Global Logistics Ltd", "po_number": "PO-1001", "invoice_date": "2025-03-14",
           "currency": "USD", "total_amount": "1060.00",
           "line_items": [{"line_number": 1, "item_code": "SKU-001", "description": "Film", "quantity": "50", "unit_price": "14.00", "line_total": "700.00"}]}


def state(**over):
    base = {"invoice_id": IID, "status": "NEEDS_REPORT_REVIEW", "awaiting_review": True, "human_check_count": 0, "max_human_checks": 3,
            "remaining_checks": 4, "validation_run": 1, "validation_status": "failed", "recommendation": "review", "invoice": INVOICE,
            "corrections": {}, "report": REPORT, "ai_audit_report": REPORT, "warnings": []}
    base.update(over)
    return base


def doc(**over):
    base = {"document_id": "a" * 16, "checksum": "a" * 64, "filename": "inv.docx", "file_type": "docx", "uploaded_at": "2026-11-19T10:00:00+00:00",
            "processing_status": "review", "audit_result": "not_available", "invoice_id": IID, "invoice_number": "INV-1001",
            "vendor_name": "Global Logistics Ltd", "currency": "USD", "total_amount": "1060.00", "findings": [], "reasons": [], "summary": None}
    base.update(over)
    return base


@pytest.fixture
def backend(monkeypatch):
    """Records review submissions; every network call is replaced."""
    calls = {"submitted": [], "retried": []}
    cfg = {"doc": doc(), "state": state()}
    monkeypatch.setattr(api_client, "get_document", lambda _id: cfg["doc"])
    monkeypatch.setattr(api_client, "review_state", lambda _id: cfg["state"])
    monkeypatch.setattr(api_client, "get_original", lambda _id: b"PK")
    monkeypatch.setattr(api_client, "review_history", lambda _id: [])
    monkeypatch.setattr(api_client, "retry_invoice", lambda _id: calls["retried"].append(_id) or {"status": "queued"})

    def submit(_id, body):
        calls["submitted"].append(body)
        return cfg.get("response", {"invoice_id": _id, "human_check_count": 1, "status": "NEEDS_REPORT_REVIEW", "validation_status": "passed",
                                    "recommendation": "approve", "discrepancies_count": 0, "validation_run": 2, "warnings": []})

    monkeypatch.setattr(api_client, "submit_review", submit)
    return calls, cfg
