"""The review dialog (rendered through Streamlit's AppTest with every backend call replaced)."""
from streamlit.testing.v1 import AppTest

from tests.conftest import IID, REPORT, doc, state


def _run():
    from app import report

    report.render_report("a" * 16, close=lambda: None)


def _app() -> AppTest:
    return AppTest.from_function(_run, default_timeout=20)


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_pending_invoice_shows_the_ai_report_review_form_and_check_counter(backend):
    at = _app().run()
    assert not at.exception
    labels = {b.label for b in at.button}
    assert {"Approve", "Reject & revalidate", "Reject"} <= labels
    assert any("Awaiting your review" in c.value for c in at.caption)
    assert any("up to 3 more re-validation" in i.value for i in at.info)
    assert any(m.label == "Recommendation" and m.value == "Needs manual review" for m in at.metric)
    assert {t.label for t in at.text_input} >= {"Invoice number", "Vendor", "Purchase order", "Your name"}
    assert [t.label for t in at.text_area] == ["Comments (optional)"]


def test_the_ai_report_section_has_no_editable_widgets(backend):
    at = _app().run()
    # the only inputs are inside the review form; nothing is bound to the report content itself
    assert all(t.key and t.key.split("_")[0] in ("h", "who") for t in at.text_input)
    assert not any("report" in (t.key or "") for t in at.text_input) and not at.text_area[0].key.startswith("report")


def test_blank_reviewer_is_rejected_client_side_and_nothing_is_sent(backend):
    calls, _ = backend
    at = _app().run()
    _button(at, "Approve").click().run()
    assert any("Enter your name" in e.value for e in at.error) and calls["submitted"] == []


def test_approve_sends_decision_and_reviewer(backend):
    calls, cfg = backend
    cfg["response"] = {"invoice_id": IID, "human_check_count": 1, "status": "APPROVED", "validation_status": "failed",
                       "recommendation": "review", "discrepancies_count": 1, "validation_run": 1, "warnings": []}
    at = _app().run()
    next(t for t in at.text_input if t.label == "Your name").set_value("auditor_jane")
    at.text_area[0].set_value("all good")
    _button(at, "Approve").click().run()
    assert calls["submitted"] == [{"decision": "approve", "reviewer": "auditor_jane", "comments": "all good", "field_corrections": []}]
    assert any("approved and finalized" in s.value for s in at.success)


def test_reject_and_revalidate_requires_a_change_then_sends_the_correction(backend):
    calls, _ = backend
    at = _app().run()
    next(t for t in at.text_input if t.label == "Your name").set_value("auditor_jane")
    _button(at, "Reject & revalidate").click().run()
    assert any("Change at least one field" in e.value for e in at.error) and calls["submitted"] == []
    next(t for t in at.text_input if t.label == "Purchase order").set_value("PO-2026-8841")
    _button(at, "Reject & revalidate").click().run()
    (body,) = calls["submitted"]
    assert body["decision"] == "reject_and_revalidate" and body["reviewer"] == "auditor_jane"
    assert body["field_corrections"] == [{"field_name": "po_number", "original_value": "PO-1001", "corrected_value": "PO-2026-8841"}]
    assert any("re-validated (run 2)" in s.value for s in at.success)


def test_last_allowed_review_warns_and_cache_warning_is_surfaced(backend):
    calls, cfg = backend
    cfg["state"] = state(human_check_count=3, validation_run=4, warnings=["The assistant's cached answers could not be cleared"])
    at = _app().run()
    assert any("last review" in w.value for w in at.warning) and any("cached answers could not be cleared" in w.value for w in at.warning)
    assert {t.label for t in at.tabs} == {"Latest validation", "Original AI report (read-only)"}  # original shown, read-only


def test_finalized_invoice_is_read_only(backend):
    _, cfg = backend
    cfg["doc"] = doc(processing_status="audited", audit_result="approved")
    cfg["state"] = state(status="APPROVED", awaiting_review=False, human_check_count=1, remaining_checks=0)
    at = _app().run()
    assert not at.exception and not {"Approve", "Reject"} & {b.label for b in at.button}
    assert any("Approved" in c.value for c in at.caption)


def test_stalled_processing_offers_resume(backend):
    calls, cfg = backend
    cfg["doc"] = doc(processing_status="processing")
    cfg["state"] = state(status="PROCESSING", awaiting_review=False, report=None, ai_audit_report=None, invoice=None)
    at = _app().run()
    _button(at, "Resume stalled processing").click().run()
    assert calls["retried"] == [IID]


def test_api_error_on_submit_is_shown_not_swallowed(backend, monkeypatch):
    from app import api_client

    def boom(_id, _body):
        raise api_client.ApiError("This invoice has already been finalized (APPROVED).", "http")

    monkeypatch.setattr(api_client, "submit_review", boom)
    at = _app().run()
    next(t for t in at.text_input if t.label == "Your name").set_value("jane")
    _button(at, "Approve").click().run()
    assert any("already been finalized" in e.value for e in at.error)
    assert REPORT["summary"]


def test_pdf_preview_renders_pages_as_images_not_a_data_url_iframe(backend):
    import pymupdf

    from app import report

    doc = pymupdf.open()
    for n in range(12):
        doc.new_page().insert_text((50, 72), f"page {n + 1}")
    pdf = doc.tobytes()
    pages, total = report.pdf_pages(pdf)
    assert total == 12 and len(pages) == report.MAX_PREVIEW_PAGES and all(p.startswith(b"\x89PNG") for p in pages)
    assert "data:application/pdf" not in __import__("inspect").getsource(report)
