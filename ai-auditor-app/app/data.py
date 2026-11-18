"""Cached read loaders (short TTL; never cache mutations). Cache key includes all filters."""
from __future__ import annotations

import streamlit as st

from app.api.services_client import get_services
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
    VendorStat,
)


@st.cache_data(ttl=10, show_spinner=False)
def summary() -> Summary:
    return get_services().summary()


@st.cache_data(ttl=10, show_spinner=False)
def by_vendor() -> list[VendorStat]:
    return get_services().by_vendor()


@st.cache_data(ttl=10, show_spinner=False)
def by_day() -> list[DayStat]:
    return get_services().by_day()


@st.cache_data(ttl=10, show_spinner=False)
def discrepancy_types() -> list[DiscrepancyTypeStat]:
    return get_services().discrepancy_types()


@st.cache_data(ttl=10, show_spinner=False)
def invoices(filters: InvoiceFilters, page: int, page_size: int) -> InvoicePage:
    return get_services().list_invoices(filters, page, page_size)


@st.cache_data(ttl=5, show_spinner=False)
def invoice(invoice_id: str) -> Invoice:
    return get_services().get_invoice(invoice_id)


@st.cache_data(ttl=5, show_spinner=False)
def line_items(invoice_id: str) -> list[LineItem]:
    return get_services().line_items(invoice_id)


@st.cache_data(ttl=5, show_spinner=False)
def discrepancies(invoice_id: str) -> list[Discrepancy]:
    return get_services().discrepancies(invoice_id, "all")


@st.cache_data(ttl=30, show_spinner=False)
def text(invoice_id: str, kind: str) -> InvoiceText:
    return get_services().text(invoice_id, kind)


@st.cache_data(ttl=5, show_spinner=False)
def events(invoice_id: str) -> list[ProcessingEvent]:
    return get_services().events(invoice_id)


@st.cache_data(ttl=5, show_spinner=False)
def feedback(invoice_id: str) -> list[Feedback]:
    return get_services().feedback(invoice_id)


@st.cache_data(ttl=30, show_spinner=False)
def report(invoice_id: str, fmt: str, run: int) -> bytes:
    """`run` is part of the key so a re-validation refreshes the report."""
    return get_services().report(invoice_id, fmt)


def clear() -> None:
    st.cache_data.clear()
