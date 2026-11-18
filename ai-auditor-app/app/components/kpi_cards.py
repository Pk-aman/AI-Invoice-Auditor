from __future__ import annotations

import streamlit as st

from app import ui_text as T
from app.formatting import percent
from app.models import Summary


def render(s: Summary) -> None:
    rec = s.by_recommendation
    cols = st.columns(6)
    cols[0].metric(T.KPI_TOTAL, s.total, border=True)
    cols[1].metric(f"{T.RECOMMENDATION['approve'][1]} {T.KPI_APPROVED}", rec.get("approve", 0),
                   border=True)
    cols[2].metric(f"{T.RECOMMENDATION['review'][1]} {T.KPI_REVIEW}", rec.get("review", 0),
                   border=True)
    cols[3].metric(f"{T.RECOMMENDATION['reject'][1]} {T.KPI_REJECTED}", rec.get("reject", 0),
                   border=True)
    cols[4].metric(T.KPI_CONFIDENCE, percent(s.avg_translation_confidence),
                   help=T.KPI_CONFIDENCE_HELP, border=True)
    cols[5].metric(T.KPI_PENDING, s.pending_corrections, help=T.KPI_PENDING_HELP, border=True)
