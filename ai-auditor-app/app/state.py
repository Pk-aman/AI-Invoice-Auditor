"""Session-state keys and helpers (one place so pages never mistype a key)."""
from __future__ import annotations

import uuid

import streamlit as st

SELECTED_INVOICE = "selected_invoice_id"
PICKER = "invoice_picker"
AUDITOR_NAME = "auditor_name"
UPLOAD_BATCH = "upload_batch"
CHAT = "chat_messages"
CHAT_SESSION = "chat_session_id"
PENDING_QUESTION = "pending_question"
FAILED_QUESTION = "failed_question"
REVALIDATING = "revalidating_invoice_id"
TABLE_ROWS_APPLIED = "table_rows_applied"


def get(key: str, default=None):
    return st.session_state.get(key, default)


def chat_session_id() -> str:
    if CHAT_SESSION not in st.session_state:
        st.session_state[CHAT_SESSION] = uuid.uuid4().hex
    return st.session_state[CHAT_SESSION]


def open_invoice(invoice_id: str | None) -> None:
    st.session_state[SELECTED_INVOICE] = invoice_id
    st.session_state[PICKER] = invoice_id
