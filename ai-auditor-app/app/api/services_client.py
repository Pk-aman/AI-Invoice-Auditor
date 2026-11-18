"""Typed wrapper over ai-auditor-services (data API under /api/v1)."""
from __future__ import annotations

import httpx

from app.api.http import cached_client, parse, parse_list, send
from app.config import get_settings
from app.models import (
    DayStat,
    Discrepancy,
    DiscrepancyTypeStat,
    Feedback,
    Invoice,
    InvoiceFilters,
    InvoicePage,
    InvoiceText,
    LineItem,
    ProcessingEvent,
    Summary,
    UploadResult,
    UploadStatus,
    VendorStat,
)

API = "/api/v1"


class ServicesClient:
    def __init__(self, client: httpx.Client):
        self._c = client

    # ---- invoices ----------------------------------------------------------
    def list_invoices(
        self, filters: InvoiceFilters | None = None, page: int = 1, page_size: int = 25,
        sort: str = "-processed_at",
    ) -> InvoicePage:
        params = {**(filters.as_params() if filters else {}), "page": page,
                  "page_size": page_size, "sort": sort}
        return parse(InvoicePage, send(self._c, "GET", f"{API}/invoices", params=params).json())

    def get_invoice(self, invoice_id: str) -> Invoice:
        return parse(Invoice, send(self._c, "GET", f"{API}/invoices/{invoice_id}").json())

    def line_items(self, invoice_id: str) -> list[LineItem]:
        return parse_list(
            LineItem, send(self._c, "GET", f"{API}/invoices/{invoice_id}/line-items").json())

    def discrepancies(self, invoice_id: str, run: str = "all") -> list[Discrepancy]:
        r = send(self._c, "GET", f"{API}/invoices/{invoice_id}/discrepancies", params={"run": run})
        return parse_list(Discrepancy, r.json())

    def text(self, invoice_id: str, kind: str) -> InvoiceText:
        r = send(self._c, "GET", f"{API}/invoices/{invoice_id}/text", params={"kind": kind})
        return parse(InvoiceText, r.json())

    def events(self, invoice_id: str) -> list[ProcessingEvent]:
        return parse_list(
            ProcessingEvent, send(self._c, "GET", f"{API}/invoices/{invoice_id}/events").json())

    def report(self, invoice_id: str, fmt: str = "html") -> bytes:
        r = send(self._c, "GET", f"{API}/invoices/{invoice_id}/report", params={"format": fmt})
        return r.content

    def feedback(self, invoice_id: str) -> list[Feedback]:
        return parse_list(
            Feedback, send(self._c, "GET", f"{API}/invoices/{invoice_id}/feedback").json())

    def add_feedback(self, invoice_id: str, field_name: str, corrected_value: str,
                     corrected_by: str) -> Feedback:
        body = {"field_name": field_name, "corrected_value": corrected_value,
                "corrected_by": corrected_by}
        r = send(self._c, "POST", f"{API}/invoices/{invoice_id}/feedback", json=body)
        return parse(Feedback, r.json())

    # ---- stats -------------------------------------------------------------
    def summary(self) -> Summary:
        return parse(Summary, send(self._c, "GET", f"{API}/stats/summary").json())

    def by_vendor(self) -> list[VendorStat]:
        return parse_list(VendorStat, send(self._c, "GET", f"{API}/stats/by-vendor").json())

    def by_day(self) -> list[DayStat]:
        return parse_list(DayStat, send(self._c, "GET", f"{API}/stats/by-day").json())

    def discrepancy_types(self) -> list[DiscrepancyTypeStat]:
        return parse_list(
            DiscrepancyTypeStat, send(self._c, "GET", f"{API}/stats/discrepancy-types").json())

    # ---- uploads -----------------------------------------------------------
    def upload(self, files: list[tuple[str, bytes, str]]) -> list[UploadResult]:
        multipart = [("files", (name, data, mime)) for name, data, mime in files]
        r = send(self._c, "POST", f"{API}/uploads", files=multipart)
        return parse_list(UploadResult, r.json())

    def upload_status(self, checksum: str) -> UploadStatus:
        return parse(UploadStatus, send(self._c, "GET", f"{API}/uploads/{checksum}/status").json())


def get_services() -> ServicesClient:
    s = get_settings()
    return ServicesClient(cached_client(s.services_url, s.services_timeout_s))
