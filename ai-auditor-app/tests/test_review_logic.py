from app import review_logic as rl
from tests.conftest import INVOICE


def test_no_edits_means_no_corrections():
    header = {f: str(INVOICE[f]) for f, _ in rl.HEADER_FIELDS}
    lines = [{c: ln[c] for c, _ in rl.LINE_COLUMNS} for ln in INVOICE["line_items"]]
    assert rl.diff_corrections(INVOICE, header, lines) == []


def test_numbers_compare_numerically_not_textually():
    header = {f: str(INVOICE[f]) for f, _ in rl.HEADER_FIELDS} | {"total_amount": "1060"}
    lines = [{"unit_price": "14", "quantity": "50.0"}]
    assert rl.diff_corrections(INVOICE, header, lines) == []


def test_edits_become_field_corrections_with_original_values():
    header = {f: str(INVOICE[f]) for f, _ in rl.HEADER_FIELDS} | {"po_number": " PO-2026-8841 "}
    lines = [{"unit_price": "12.00", "description": "Film"}]
    got = {c["field_name"]: c for c in rl.diff_corrections(INVOICE, header, lines)}
    assert set(got) == {"po_number", "line:1:unit_price"}
    assert got["po_number"] == {"field_name": "po_number", "original_value": "PO-1001", "corrected_value": "PO-2026-8841"}
    assert got["line:1:unit_price"]["original_value"] == "14.00" and got["line:1:unit_price"]["corrected_value"] == "12.00"


def test_validation_mirrors_api_rules():
    c = [{"field_name": "po_number", "corrected_value": "X"}]
    assert rl.validate_submission("approve", "", []) and rl.validate_submission("approve", "   ", [])
    assert rl.validate_submission("reject_and_revalidate", "jane", [])  # needs a change
    assert rl.validate_submission("approve", "jane", c)  # approve cannot carry edits
    assert rl.validate_submission("approve", "jane", []) is None
    assert rl.validate_submission("reject_and_revalidate", "jane", c) is None
    assert rl.validate_submission("reject", "jane", []) is None


def test_checks_message_never_promises_more_than_the_limit():
    assert rl.checks_message({"awaiting_review": True, "human_check_count": 0, "max_human_checks": 3})[1].endswith("up to 3 more re-validation(s).")
    level, text = rl.checks_message({"awaiting_review": True, "human_check_count": 3, "max_human_checks": 3})
    assert level == "warning" and "last review" in text and "review limit reached" in text
    assert rl.checks_message({"awaiting_review": False, "human_check_count": 2, "max_human_checks": 3}) == ("info", "Reviews completed: 2.")


def test_response_notices():
    assert rl.response_notice({"status": "APPROVED"})[0] == "success"
    assert "rejected" in rl.response_notice({"status": "REJECTED"})[1]
    level, text = rl.response_notice({"status": "SKIPPED_MAX_RETRIES", "discrepancies_count": 2})
    assert level == "warning" and "review limit reached" in text and "2 issue" in text
    assert "No issues remain" in rl.response_notice({"status": "NEEDS_REPORT_REVIEW", "discrepancies_count": 0, "validation_run": 2})[1]
