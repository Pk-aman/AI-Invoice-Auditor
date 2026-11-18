"""Page 3: Invoice Assistant (RAG chat)."""
from __future__ import annotations

import streamlit as st

from app import data, state
from app import ui_text as T
from app.api.agent_client import get_agent
from app.api.http import UiError
from app.components import chat_message
from app.config import get_settings
from app.models import InvoiceFilters, RagEvaluation


def _evals() -> dict[str, RagEvaluation]:
    return st.session_state.setdefault("rag_evals", {})


def _pending_ids(messages: list[dict]) -> list[str]:
    ev = _evals()
    return [m["eval_id"] for m in messages
            if m["role"] == "assistant" and m.get("eval_id")
            and (ev.get(m["eval_id"]) is None or ev[m["eval_id"]].state.upper() == "PENDING")]


def _poll_scores() -> None:
    """Fill in RAGAS scores as they become available, then stop polling."""
    agent = get_agent()
    for eid in _pending_ids(st.session_state.get(state.CHAT, [])):
        try:
            _evals()[eid] = agent.rag_evaluation(eid)
        except UiError:
            _evals()[eid] = RagEvaluation(state="FAILED")
    if not _pending_ids(st.session_state.get(state.CHAT, [])):
        st.rerun(scope="app")


def _scope() -> tuple[str | None, str | None]:
    scope = st.radio(T.SCOPE_LABEL, [T.SCOPE_ALL, T.SCOPE_INVOICE, T.SCOPE_VENDOR], horizontal=True)
    try:
        if scope == T.SCOPE_INVOICE:
            items = data.invoices(InvoiceFilters(), 1, 100).items
            if not items:
                st.caption(T.SCOPE_NO_DATA)
                return None, None
            labels = {i.invoice_id: f"{i.invoice_number or '(no number)'} · "
                                    f"{i.vendor_name or 'unknown vendor'}" for i in items}
            return st.selectbox(T.SCOPE_PICK_INVOICE, list(labels), format_func=labels.get), None
        if scope == T.SCOPE_VENDOR:
            vendors = [v.vendor for v in data.by_vendor()]
            if not vendors:
                st.caption(T.SCOPE_NO_DATA)
                return None, None
            return None, st.selectbox(T.SCOPE_PICK_VENDOR, vendors)
    except UiError as err:
        st.error(err.message)
    return None, None


def _ask(question: str, invoice_id: str | None, vendor: str | None) -> None:
    msgs = st.session_state.setdefault(state.CHAT, [])
    msgs.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner(T.CHAT_THINKING):
                ans = get_agent().rag_query(question, invoice_id, vendor, state.chat_session_id())
        except UiError as err:
            st.session_state[state.FAILED_QUESTION] = (question, invoice_id, vendor, err.message)
            msgs.pop()
            return
        st.session_state.pop(state.FAILED_QUESTION, None)
        msg = {"role": "assistant", "content": ans.answer, "route": ans.route,
               "sources": ans.sources, "eval_id": ans.eval_id}
        msgs.append(msg)
        chat_message.render_assistant(msg, None)


def render() -> None:
    st.title(T.ASSIST_TITLE)
    st.caption(T.ASSIST_INTRO)
    invoice_id, vendor = _scope()

    top = st.columns([6, 1])
    top[0].caption(f"**{T.SUGGESTED}**")
    if top[1].button(T.CHAT_CLEAR, disabled=not st.session_state.get(state.CHAT)):
        st.session_state[state.CHAT] = []
        st.session_state["rag_evals"] = {}
        st.rerun()
    scols = st.columns(len(T.SUGGESTIONS))
    for i, q in enumerate(T.SUGGESTIONS):
        if scols[i].button(q, key=f"sugg_{i}", width="stretch"):
            st.session_state[state.PENDING_QUESTION] = q

    messages = st.session_state.setdefault(state.CHAT, [])
    if not messages:
        st.caption(T.CHAT_EMPTY)

    def _history() -> None:
        for m in messages:
            with st.chat_message(m["role"]):
                if m["role"] == "user":
                    st.markdown(m["content"])
                else:
                    chat_message.render_assistant(m, _evals().get(m.get("eval_id") or ""))

    # Quality scores arrive later; this fragment re-polls only while some are pending.
    every = get_settings().poll_upload_s if _pending_ids(messages) else None
    st.fragment(run_every=every)(lambda: (_poll_scores() if _pending_ids(messages) else None,
                                          _history()))()

    failed = st.session_state.get(state.FAILED_QUESTION)
    if failed:
        st.error(f"{T.CHAT_ERROR} {failed[3]}")
        if st.button(T.BTN_TRY_AGAIN, key="assist_retry"):
            _ask(failed[0], failed[1], failed[2])
            st.rerun()

    prompt = st.chat_input(T.CHAT_PLACEHOLDER) or st.session_state.pop(state.PENDING_QUESTION, None)
    if prompt:
        _ask(prompt, invoice_id, vendor)
        st.rerun()
