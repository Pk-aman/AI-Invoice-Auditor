"""Corrections tab: editable fields, confirm dialog, history. Never overwrites originals."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import corrections as C
from app import data, state
from app import ui_text as T
from app.api.http import UiError
from app.api.services_client import get_services
from app.formatting import field_label, pretty_datetime
from app.models import Feedback, Invoice, LineItem


@st.dialog(T.CORR_DIALOG_TITLE, width="large")
def _confirm(invoice_id: str, changes: list[C.Change], who: str) -> None:
    st.dataframe(
        pd.DataFrame([{T.COL_FIELD: field_label(c.field_name), T.COL_WAS: c.old or "—",
                       T.COL_NOW: c.new} for c in changes]),
        hide_index=True, width="stretch")
    left, right = st.columns(2)
    if left.button(T.CORR_CONFIRM, type="primary", width="stretch", key="corr_confirm"):
        try:
            svc = get_services()
            for c in changes:
                svc.add_feedback(invoice_id, c.field_name, c.new, who)
        except UiError as err:
            st.error(err.message)
            return
        data.clear()
        st.session_state["flash"] = T.CORR_SAVED.format(n=len(changes))
        st.rerun()
    if right.button(T.CORR_CANCEL, width="stretch", key="corr_cancel"):
        st.rerun()


def render(inv: Invoice, lines: list[LineItem], history: list[Feedback]) -> None:
    st.caption(T.CORR_INTRO)
    effective = C.effective_values(inv, lines, history)
    ver = len(history)
    edited: dict[str, str] = {}

    with st.form(f"corrections_{inv.invoice_id}_{ver}"):
        who = st.text_input(T.CORR_NAME, key=state.AUDITOR_NAME, help=T.CORR_NAME_HELP)
        st.markdown(f"**{T.CORR_HEADER}**")
        cols = st.columns(2)
        for i, f in enumerate(C.HEADER_FIELDS):
            edited[f] = cols[i % 2].text_input(
                field_label(f), value=effective.get(f, ""), key=f"corr_{inv.invoice_id}_{ver}_{f}")
        grid = None
        if lines:
            st.markdown(f"**{T.CORR_LINES}**")
            rows = []
            for i, li in enumerate(lines, start=1):
                n = li.line_number or i
                rows.append({"Line": n, **{col: effective.get(C.line_field_name(n, col), "")
                                           for col in C.LINE_FIELDS}})
            grid = st.data_editor(pd.DataFrame(rows).astype(str), hide_index=True,
                                  disabled=["Line"], width="stretch",
                                  key=f"lines_{inv.invoice_id}_{ver}")
        submitted = st.form_submit_button(T.CORR_SAVE, type="primary")

    if grid is not None:
        for _, row in grid.iterrows():
            for col in C.LINE_FIELDS:
                edited[C.line_field_name(int(row["Line"]), col)] = str(row[col])

    if submitted:
        changes = [C.normalise(c) for c in C.diff(effective, edited)]
        if not changes:
            st.info(T.CORR_NO_CHANGES)
        elif not (who or "").strip():
            st.warning(T.CORR_NEED_NAME)
        else:
            problems = C.validate(changes)
            if problems:
                for p in problems:
                    st.error(p)
            else:
                _confirm(inv.invoice_id, changes, who.strip())

    st.markdown(f"**{T.CORR_HISTORY}**")
    if not history:
        st.caption(T.CORR_HISTORY_EMPTY)
    else:
        st.dataframe(
            pd.DataFrame([{
                T.COL_FIELD: field_label(h.field_name), T.COL_WAS: h.original_value or "—",
                T.COL_NOW: h.corrected_value or "—", T.COL_BY: h.corrected_by or "—",
                T.COL_WHEN: pretty_datetime(h.corrected_at),
                T.COL_CHECKED: "✅ Yes" if h.revalidated else "⏳ Not yet",
            } for h in sorted(history, key=lambda h: h.id or 0, reverse=True)]),
            hide_index=True, width="stretch")
