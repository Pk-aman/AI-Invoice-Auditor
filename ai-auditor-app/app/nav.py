"""Navigation: pages are built once so pages can link/switch to each other."""
from __future__ import annotations

import streamlit as st

from app import ui_text as T
from app.pages import assistant, dashboard, upload

_PAGES: dict[str, st.Page] = {}


def pages() -> dict[str, st.Page]:
    if not _PAGES:
        _PAGES["dashboard"] = st.Page(dashboard.render, title=T.NAV_DASHBOARD, icon="📊",
                                      url_path="dashboard", default=True)
        _PAGES["upload"] = st.Page(upload.render, title=T.NAV_UPLOAD, icon="📤", url_path="upload")
        _PAGES["assistant"] = st.Page(assistant.render, title=T.NAV_ASSISTANT, icon="💬",
                                      url_path="assistant")
    return _PAGES


def dashboard_page() -> st.Page:
    return pages()["dashboard"]


def upload_page() -> st.Page:
    return pages()["upload"]
