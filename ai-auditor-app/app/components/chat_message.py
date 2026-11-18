"""Render one assistant answer: text, route badge, sources, async quality scores."""
from __future__ import annotations

import streamlit as st

from app import ui_text as T
from app.models import RagEvaluation, Source


def route_label(route: str | None) -> tuple[str, str]:
    return T.ROUTE_DB if (route or "").lower() in T.ROUTE_DB_KEYS else T.ROUTE_DOCS


def _source_title(s: Source) -> str:
    kind = T.SOURCE_REPORT if (s.source_type or "").lower() == "report" else T.SOURCE_INVOICE
    who = s.invoice_number or (s.invoice_id or "")[:8] or "invoice"
    return f"{who} · {kind}"


def scores(ev: RagEvaluation | None) -> None:
    st.caption(f"**{T.SCORES_TITLE}**")
    if ev is None or ev.state.upper() == "PENDING":
        st.caption(T.SCORES_PENDING)
        return
    if ev.state.upper() != "READY":
        st.caption(T.SCORES_NA)
        return
    cols = st.columns(3)
    for col, label, val, hlp in (
        (cols[0], T.SCORE_RELEVANCE, ev.relevance, T.SCORE_RELEVANCE_HELP),
        (cols[1], T.SCORE_GROUNDED, ev.groundedness, T.SCORE_GROUNDED_HELP),
        (cols[2], T.SCORE_CONTEXT, ev.context_relevance, T.SCORE_CONTEXT_HELP),
    ):
        col.metric(label, "—" if val is None else f"{val * 100:.0f}%", help=hlp)


def render_assistant(msg: dict, ev: RagEvaluation | None) -> None:
    st.markdown(msg["content"])
    label, icon = route_label(msg.get("route"))
    st.badge(label, icon=icon, color="blue")
    sources: list[Source] = msg.get("sources") or []
    if sources:
        with st.expander(T.SOURCES.format(n=len(sources))):
            for s in sources:
                st.markdown(f"**{_source_title(s)}**")
                st.caption(s.chunk_text[:600])
    if msg.get("eval_id"):
        scores(ev)
