"""Accessible status badges: icon + text + colour."""
from __future__ import annotations

import streamlit as st

from app import ui_text as T
from app.formatting import outcome
from app.models import Invoice


def recommendation_badge(inv: Invoice) -> None:
    label, icon, color = outcome(inv)
    st.badge(label, icon=icon, color=color)


def checks_badge(status: str | None) -> None:
    label, icon, color = T.VALIDATION.get(status or "", T.UNKNOWN)
    st.badge(label, icon=icon, color=color)


def severity_badge(sev: str | None) -> None:
    label, icon = T.SEVERITY.get((sev or "").lower(), (sev or "—", "⚪"))
    st.badge(label, icon=icon, color={"low": "green", "medium": "yellow", "high": "orange",
                                      "critical": "red"}.get((sev or "").lower(), "gray"))
