"""All backend communication lives here (REST via requests). The UI never touches a database."""
from __future__ import annotations

import requests

from app.config import AGENT_URL, SERVICES_URL

_session = requests.Session()


class ApiError(Exception):
    """kind: unreachable | not_found | http"""

    def __init__(self, message: str, kind: str = "http"):
        super().__init__(message)
        self.kind = kind


def _message(resp: requests.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return err["message"]
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict) and detail.get("message"):  # agent review errors: {"code", "message"}
            return str(detail["message"])
        if detail:
            return str(detail)
    except ValueError:
        pass
    return f"The service returned an error ({resp.status_code})."


def _request(method: str, url: str, *, timeout=(3, 15), **kw) -> requests.Response:
    try:
        resp = _session.request(method, url, timeout=timeout, **kw)
    except (requests.ConnectionError, requests.Timeout):
        raise ApiError("The service could not be reached.", "unreachable") from None
    if resp.status_code == 404:
        raise ApiError(_message(resp), "not_found")
    if resp.status_code >= 400:
        raise ApiError(_message(resp), "http")
    return resp


# ---- ai-auditor-services ---------------------------------------------------
def document_stats() -> dict:
    return _request("GET", f"{SERVICES_URL}/api/v1/documents/stats").json()


def list_documents(params: dict) -> dict:
    return _request("GET", f"{SERVICES_URL}/api/v1/documents", params=params).json()


def get_document(document_id: str) -> dict:
    return _request("GET", f"{SERVICES_URL}/api/v1/documents/{document_id}").json()


def get_original(document_id: str) -> bytes:
    return _request("GET", f"{SERVICES_URL}/api/v1/documents/{document_id}/file", timeout=(3, 30)).content


def erp_overview() -> dict:
    return _request("GET", f"{SERVICES_URL}/api/v1/erp/overview", timeout=(3, 30)).json()


def upload(files: list[tuple[str, bytes, str]]) -> list[dict]:
    payload = [("files", (name, data, mime)) for name, data, mime in files]
    return _request("POST", f"{SERVICES_URL}/api/v1/uploads", files=payload, timeout=(3, 120)).json()


# ---- agent-service ---------------------------------------------------------
def ask(question: str, session_id: str, invoice_id: str | None = None) -> dict:
    body = {"question": question, "session_id": session_id}
    if invoice_id:
        body["invoice_id"] = invoice_id
    return _request("POST", f"{AGENT_URL}/rag/query", json=body, timeout=(3, 240)).json()


def review_state(invoice_id: str) -> dict:
    """Live review state (checkpoint while pending, PostgreSQL once finalized)."""
    return _request("GET", f"{AGENT_URL}/invoices/{invoice_id}/review", timeout=(3, 30)).json()


def submit_review(invoice_id: str, body: dict) -> dict:
    """Approve / reject / reject_and_revalidate. Re-validation calls the LLM, so allow time."""
    return _request("POST", f"{AGENT_URL}/invoices/{invoice_id}/review", json=body, timeout=(3, 300)).json()


def retry_invoice(invoice_id: str) -> dict:
    return _request("POST", f"{AGENT_URL}/invoices/{invoice_id}/retry", timeout=(3, 30)).json()


def review_history(invoice_id: str) -> list[dict]:
    return _request("GET", f"{SERVICES_URL}/api/v1/invoices/{invoice_id}/reviews").json()


def evaluation(eval_id: str) -> dict:
    return _request("GET", f"{AGENT_URL}/rag/evaluations/{eval_id}").json()
