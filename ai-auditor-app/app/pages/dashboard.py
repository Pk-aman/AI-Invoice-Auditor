"""Page 1: Dashboard. KPIs, charts, filters, invoice table, and the review/correct panel."""
from __future__ import annotations

import streamlit as st

from app import data, state
from app import ui_text as T
from app.api.http import UiError
from app.components import charts, invoice_detail, invoice_table, kpi_cards
from app.config import get_settings
from app.models import InvoiceFilters

PAGE_SIZE = 15


def _filters() -> tuple[InvoiceFilters, int]:
    with st.expander(T.FILTERS, expanded=False):
        c = st.columns(3)
        try:
            vendors = [v.vendor for v in data.by_vendor()]
        except UiError:
            vendors = []
        vendor = c[0].selectbox(T.FILTER_VENDOR, [T.FILTER_ALL, *vendors])
        status = c[1].selectbox(T.FILTER_STATUS, [T.FILTER_ALL, *T.VALIDATION],
                                format_func=lambda k: k if k == T.FILTER_ALL else T.VALIDATION[k][0])
        rec = c[2].selectbox(T.FILTER_REC, [T.FILTER_ALL, *T.RECOMMENDATION],
                             format_func=lambda k: k if k == T.FILTER_ALL else T.RECOMMENDATION[k][0])
        c2 = st.columns(3)
        currency = c2[0].selectbox(T.FILTER_CURRENCY, [T.FILTER_ALL, "USD", "EUR", "INR", "GBP", "VND"])
        dates = c2[1].date_input(T.FILTER_DATES, value=(), format="YYYY-MM-DD")
        q = c2[2].text_input(T.FILTER_SEARCH)
        page = int(st.number_input(T.PAGE_LABEL, min_value=1, value=1, step=1))
    pick = lambda v: None if v == T.FILTER_ALL else v  # noqa: E731
    d_from = dates[0] if len(dates) > 0 else None
    d_to = dates[1] if len(dates) > 1 else None
    return InvoiceFilters(vendor=pick(vendor), status=pick(status), recommendation=pick(rec),
                          currency=pick(currency), date_from=d_from, date_to=d_to,
                          q=q.strip() or None), page


def _live_region(filters: InvoiceFilters, page: int) -> None:
    """Auto-refreshing part: KPIs, charts, table, picker (detail panel stays outside)."""
    try:
        summary = data.summary()
        result = data.invoices(filters, page, PAGE_SIZE)
    except UiError as err:
        st.error(err.message)
        if st.button(T.BTN_TRY_AGAIN, key="dash_retry"):
            data.clear()
            st.rerun(scope="app")
        return

    if summary.total == 0:
        st.info(f"**{T.EMPTY_TITLE}**\n\n{T.EMPTY_BODY}")
        from app.nav import upload_page
        st.page_link(upload_page(), label=T.EMPTY_LINK, icon="📤")
        return

    kpi_cards.render(summary)
    try:
        days, vendors, types = data.by_day(), data.by_vendor(), data.discrepancy_types()
    except UiError as err:
        st.warning(err.message)
        days, vendors, types = [], [], []
    left, right = st.columns(2)
    with left:
        charts.by_recommendation(summary)
        charts.vendors(vendors)
    with right:
        charts.over_time(days)
        charts.discrepancy_types(types)

    st.subheader(T.TABLE_TITLE)
    st.caption(f"{T.TABLE_HINT} {T.LIVE_NOTE}")
    items = result.items
    if not items:
        st.info("No invoices match these filters.")
        return
    a = (result.page - 1) * result.page_size + 1
    st.caption(T.SHOWING.format(a=a, b=a + len(items) - 1, n=result.total))
    event = st.dataframe(
        invoice_table.to_frame(items), hide_index=True, width="stretch",
        on_select="rerun", selection_mode="single-row", key="invoice_table",
        column_config={"Confidence": st.column_config.ProgressColumn(
            "Confidence", min_value=0, max_value=100, format="%d%%")})
    rows = list(event.selection.rows) if event and event.selection else []
    if rows and rows[0] < len(items) and st.session_state.get(state.TABLE_ROWS_APPLIED) != rows:
        st.session_state[state.TABLE_ROWS_APPLIED] = rows
        state.open_invoice(items[rows[0]].invoice_id)
        st.rerun(scope="app")
    if not rows:
        st.session_state[state.TABLE_ROWS_APPLIED] = None

    ids = [i.invoice_id for i in items]
    selected = st.session_state.get(state.SELECTED_INVOICE)
    labels = {i.invoice_id: invoice_table.picker_label(i) for i in items}
    options = [None, *ids] if selected in (None, *ids) else [None, selected, *ids]
    choice = st.selectbox(T.PICKER_LABEL, options, key=state.PICKER,
                          format_func=lambda v: T.PICKER_NONE if v is None else labels.get(v, v))
    if choice != selected:
        st.session_state[state.SELECTED_INVOICE] = choice
        st.rerun(scope="app")


def render() -> None:
    st.title(T.DASH_TITLE)
    st.caption(T.DASH_INTRO)
    if "flash" in st.session_state and not st.session_state.get(state.SELECTED_INVOICE):
        st.toast(st.session_state.pop("flash"), icon="✅")
    filters, page = _filters()
    st.fragment(run_every=get_settings().poll_dash_s)(_live_region)(filters, page)

    selected = st.session_state.get(state.SELECTED_INVOICE)
    if selected:
        st.divider()
        invoice_detail.render(selected)
