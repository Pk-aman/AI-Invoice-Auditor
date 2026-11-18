"""Invoice detail panel: summary, lines, discrepancies, text, report, timeline, corrections."""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from app import data, state
from app import ui_text as T
from app.api.agent_client import get_agent
from app.api.http import UiError
from app.components import correction_form
from app.components.status_badge import recommendation_badge, severity_badge
from app.formatting import (
    checks_text,
    field_label,
    money,
    percent,
    pretty_date,
    pretty_datetime,
    reason,
    severity_text,
)
from app.models import Discrepancy, Invoice, LineItem

_LINE_RE = re.compile(r"^line[:_ ]?(\d+)[:_ .\-]+([a-z_ ]+)$", re.IGNORECASE)
_ALIASES = {"qty": "quantity", "price": "unit_price", "unit price": "unit_price",
            "total": "line_total"}


def line_discrepancy_map(discs: list[Discrepancy]) -> dict[tuple[int, str], Discrepancy]:
    """(line_no, column) -> current-round discrepancy. Field names like `line:2:unit_price`."""
    out: dict[tuple[int, str], Discrepancy] = {}
    for d in discs:
        if d.superseded:
            continue
        m = _LINE_RE.match(d.field_name.strip())
        if m:
            col = m.group(2).strip().lower().replace(" ", "_")
            out[(int(m.group(1)), _ALIASES.get(col, col))] = d
    return out


def line_frame(lines: list[LineItem], discs: list[Discrepancy]):
    bad = line_discrepancy_map(discs)
    rows, marks = [], []
    for i, li in enumerate(lines, start=1):
        n = li.line_number or i
        notes = [f"{field_label(c).split('–')[-1].strip()}: {d.erp_value or 'none'}"
                 for (ln, c), d in bad.items() if ln == n]
        rows.append({"Line": n, "Item code": li.item_code or "—",
                     "Description": li.description or "—",
                     "Quantity": li.quantity, "Unit price": li.unit_price,
                     "Line total": li.line_total,
                     T.ERP_SAYS: "; ".join(notes) if notes else ""})
        marks.append({c for (ln, c) in bad if ln == n})
    df = pd.DataFrame(rows)
    cols = {"quantity": "Quantity", "unit_price": "Unit price", "line_total": "Line total",
            "item_code": "Item code", "description": "Description"}

    def style(_):
        s = pd.DataFrame("", index=df.index, columns=df.columns)
        for r, cs in enumerate(marks):
            for c in cs:
                if c in cols:
                    s.loc[r, cols[c]] = "background-color: #FCE4D6; font-weight: 700"
        return s

    return df.style.apply(style, axis=None)


def _summary_tab(inv: Invoice, discs: list[Discrepancy]) -> None:
    c1, c2 = st.columns([1, 2])
    with c1:
        recommendation_badge(inv)
    c2.write(reason(inv, discs))
    cols = st.columns(3)
    pairs = [
        (field_label("invoice_number"), inv.invoice_number or "—"),
        (field_label("vendor_name"), inv.vendor_name or "—"),
        (field_label("po_number"), inv.po_number or "—"),
        (field_label("invoice_date"), pretty_date(inv.invoice_date)),
        (field_label("currency"), inv.currency or "—"),
        (field_label("total_amount"), money(inv.total_amount, inv.currency)),
        (T.LANGUAGE_LABEL, (inv.source_language or "—").upper()),
        (T.RUN_LABEL, str(inv.validation_run)),
        ("Checks", checks_text(inv.validation_status)),
    ]
    for i, (k, v) in enumerate(pairs):
        cols[i % 3].markdown(f"**{k}**  \n{v}")
    if inv.translation_confidence is not None:
        st.progress(min(max(inv.translation_confidence, 0.0), 1.0),
                    text=f"{T.CONFIDENCE_LABEL}: {percent(inv.translation_confidence)}")


def _lines_tab(inv: Invoice, lines: list[LineItem], discs: list[Discrepancy]) -> None:
    if not lines:
        st.info(T.NO_LINES)
        return
    st.dataframe(line_frame(lines, discs), hide_index=True, width="stretch")
    st.caption("Highlighted cells do not match the purchase order. The last column shows what "
               "the ERP expects.")


def _discrepancies_tab(discs: list[Discrepancy]) -> None:
    show_old = st.toggle(T.SHOW_EARLIER, value=False)
    rows = [d for d in discs if show_old or not d.superseded]
    if not rows:
        st.success(T.NO_DISCREPANCIES)
        return
    st.dataframe(pd.DataFrame([{
        T.COL_FIELD: field_label(d.field_name),
        T.COL_INVOICE_VALUE: d.invoice_value or "—", T.COL_ERP_VALUE: d.erp_value or "—",
        T.COL_DEVIATION: "—" if d.deviation_pct is None else f"{d.deviation_pct:.1f}%",
        T.COL_SEVERITY: severity_text(d.severity), T.COL_ROUND: d.validation_run,
        T.COL_STATE: T.STATE_EARLIER if d.superseded else T.STATE_CURRENT,
    } for d in rows]), hide_index=True, width="stretch")
    worst = next((d.severity for d in rows if (d.severity or "").lower() == "critical"), None)
    if worst:
        severity_badge(worst)


def _text_tab(invoice_id: str) -> None:
    left, right = st.columns(2)
    for col, kind, title in ((left, "raw", T.RAW_TEXT), (right, "translated", T.TRANSLATED_TEXT)):
        with col:
            try:
                txt = data.text(invoice_id, kind).text
            except UiError as err:
                st.error(err.message)
                continue
            st.text_area(title, value=txt, height=380, disabled=True, key=f"txt_{kind}_{invoice_id}")


def _report_tab(inv: Invoice) -> None:
    try:
        html = data.report(inv.invoice_id, "html", inv.validation_run)
    except UiError as err:
        st.info(T.REPORT_MISSING if err.status == 404 else err.message)
        return
    components.html(html.decode("utf-8", errors="replace"), height=560, scrolling=True)
    c1, c2 = st.columns(2)
    c1.download_button(T.DOWNLOAD_HTML, html, file_name=f"report_{inv.invoice_id}.html",
                       mime="text/html")
    try:
        js = data.report(inv.invoice_id, "json", inv.validation_run)
        c2.download_button(T.DOWNLOAD_JSON, js, file_name=f"report_{inv.invoice_id}.json",
                           mime="application/json")
    except UiError:
        pass


def _timeline_tab(invoice_id: str) -> None:
    evs = data.events(invoice_id)
    if not evs:
        st.caption(T.NO_EVENTS)
        return
    for e in evs:
        label, icon, _ = T.UPLOAD_STAGES.get(e.stage.upper(), (e.stage.title(), "•", 0))
        ok = {"OK": "done", "STARTED": "started", "FAILED": "failed", "DUPLICATE": "duplicate",
              "REJECTED": "not accepted"}.get(e.state.upper(), e.state.lower())
        st.markdown(f"{icon} **{label}**: {ok} · {pretty_datetime(e.created_at)}")
        if e.state.upper() == "FAILED":
            why = (e.detail or {}).get("error") or (e.detail or {}).get("message") or "unknown"
            st.warning(T.EVENT_FAILED_HINT.format(why=why))


# ---- re-validation -------------------------------------------------------------------
def _revalidate_progress(invoice_id: str) -> None:
    try:
        stt = get_agent().status(invoice_id)
    except UiError as err:
        st.session_state.pop(state.REVALIDATING, None)
        st.error(err.message)
        return
    if stt.state.upper() == "COMPLETED":
        st.session_state.pop(state.REVALIDATING, None)
        data.clear()
        st.session_state["flash"] = T.REVALIDATE_DONE
        st.rerun(scope="app")
    elif stt.state.upper() == "FAILED":
        st.session_state.pop(state.REVALIDATING, None)
        st.error(T.REVALIDATE_FAILED.format(why=stt.error or "unknown"))
    else:
        label = T.UPLOAD_STAGES.get((stt.stage or "").upper(), (T.REVALIDATE_RUNNING,))[0]
        st.info(f"🔄 {T.REVALIDATE_RUNNING} ({label})")


def _revalidate_controls(inv: Invoice, pending: bool) -> None:
    running = st.session_state.get(state.REVALIDATING) == inv.invoice_id
    clicked = st.button(T.REVALIDATE, type="primary", disabled=(not pending) or running,
                        help=T.REVALIDATE_HELP if pending else T.REVALIDATE_DISABLED,
                        key=f"revalidate_{inv.invoice_id}")
    if clicked:
        try:
            get_agent().revalidate(inv.invoice_id)
        except UiError as err:
            st.error(err.message)
            return
        st.session_state[state.REVALIDATING] = inv.invoice_id
        st.rerun()
    if running:
        st.fragment(run_every=state_poll())(_revalidate_progress)(inv.invoice_id)


def state_poll() -> int:
    from app.config import get_settings
    return get_settings().poll_upload_s


def render(invoice_id: str) -> None:
    try:
        inv = data.invoice(invoice_id)
        lines = data.line_items(invoice_id)
        discs = data.discrepancies(invoice_id)
        history = data.feedback(invoice_id)
    except UiError as err:
        st.error(err.message)
        return

    head, close = st.columns([5, 1])
    head.subheader(T.DETAIL_TITLE.format(no=inv.invoice_number or "(no number)"))
    if close.button(T.CLOSE_DETAIL, key="close_detail"):
        state.open_invoice(None)
        st.rerun()
    if "flash" in st.session_state:
        st.toast(st.session_state.pop("flash"), icon="✅")

    _revalidate_controls(inv, pending=any(not h.revalidated for h in history))

    tabs = st.tabs([T.TAB_SUMMARY, T.TAB_LINES, T.TAB_DISCREPANCIES, T.TAB_TEXT, T.TAB_REPORT,
                    T.TAB_TIMELINE, T.TAB_CORRECTIONS])
    with tabs[0]:
        _summary_tab(inv, discs)
    with tabs[1]:
        _lines_tab(inv, lines, discs)
    with tabs[2]:
        _discrepancies_tab(discs)
    with tabs[3]:
        _text_tab(invoice_id)
    with tabs[4]:
        _report_tab(inv)
    with tabs[5]:
        try:
            _timeline_tab(invoice_id)
        except UiError as err:
            st.error(err.message)
    with tabs[6]:
        correction_form.render(inv, lines, history)
