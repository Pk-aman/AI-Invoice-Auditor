import uuid

import streamlit as st

from app import api_client, ui
from app.api_client import ApiError

SUGGESTIONS = ["Which invoices need attention?", "What issues were detected?", "Summarize my invoices."]

ui.header("WORKSPACE / ASSISTANT", "Your invoice assistant.",
          "Ask questions about your invoices and their audit findings.")


def _new_chat() -> str:
    chat_id = uuid.uuid4().hex[:8]
    st.session_state.chats[chat_id] = {"title": "New chat", "messages": []}
    st.session_state.current_chat = chat_id
    return chat_id


if "chats" not in st.session_state:
    st.session_state.chats = {}
    _new_chat()

top = st.columns([4, 1], vertical_alignment="bottom")
ids = list(st.session_state.chats)
current = st.session_state.current_chat
labels, seen = {}, {}
for i in ids:  # unique, human-readable labels even if two chats share a title
    title = st.session_state.chats[i]["title"]
    seen[title] = seen.get(title, 0) + 1
    labels[i] = title if seen[title] == 1 else f"{title} ({seen[title]})"
picked = top[0].selectbox("Chat", list(labels.values()), index=ids.index(current))
picked_id = next(i for i, label in labels.items() if label == picked)
if picked_id != current:
    st.session_state.current_chat = picked_id
    st.rerun()
if top[1].button("New chat", icon=":material/add:", use_container_width=True):
    _new_chat()
    st.rerun()

chat = st.session_state.chats[st.session_state.current_chat]


@st.cache_data(ttl=10, show_spinner=False)
def _audited_invoices() -> list[dict]:
    data = api_client.list_documents({"page_size": 100})
    return [d for d in data["items"] if d.get("invoice_id") and d["processing_status"] in ("audited", "review")]


scope_id = None
try:
    docs = _audited_invoices()
except ApiError:
    docs = []
if docs:
    scope = {"All invoices": None}
    for d in docs:
        scope[f"{ui.display_name(d)} — {d.get('vendor_name') or 'vendor not extracted'}"] = d["invoice_id"]
    scope_id = scope[st.selectbox("Ask about", list(scope))]


METRIC_LABELS = (("context_relevance", "Context relevance"), ("faithfulness", "Faithfulness (grounded in the documents)"),
                 ("answer_relevance", "Answer relevance"))


def _metrics_block(message: dict, *, poll: bool) -> None:
    """RAGAS quality scores. Shown only once computed; while pending they are polled, never invented."""
    metrics = message.get("ragas_metrics") or {}
    state = metrics.get("state")
    if state == "pending" and message.get("eval_id") and poll:
        try:
            fresh = api_client.evaluation(message["eval_id"])
        except ApiError:
            fresh = None
        if fresh and fresh.get("state") != "pending":
            message["ragas_metrics"] = metrics = fresh
            state = fresh["state"]
            st.rerun(scope="app")
    if state == "done":
        cols = st.columns(3)
        for col, (key, label) in zip(cols, METRIC_LABELS):
            col.metric(label, f"{metrics[key]:.0%}")
        if metrics.get("low_confidence_warning"):
            st.warning("Low confidence: please double-check this answer against the invoice and its report.")
    elif state == "pending":
        st.caption("Checking answer quality…")
    elif state == "failed":
        st.caption("Answer quality could not be scored for this response.")


@st.fragment(run_every=3)
def _poll_metrics(message: dict) -> None:
    _metrics_block(message, poll=True)


def _render(message: dict, *, latest: bool = False) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] != "assistant":
            return
        if message.get("cache_hit"):
            st.caption(":material/bolt: Answered from a recent saved response.")
        if message.get("sources"):
            with st.expander("Sources"):
                for src in message["sources"]:
                    label = {"ai_report": "Audit report", "invoice_text": "Invoice text", "database": "Database"}.get(
                        src.get("source_type"), src.get("source_type", ""))
                    st.caption(f"{src.get('invoice_number') or src.get('invoice_id') or 'All invoices'} · {label}")
                    if src.get("chunk_text"):
                        st.write(src["chunk_text"][:600])
        if message.get("ragas_metrics") and message["ragas_metrics"].get("state") not in (None, "not_applicable", "disabled"):
            if latest and message["ragas_metrics"].get("state") == "pending":
                _poll_metrics(message)
            else:
                _metrics_block(message, poll=False)


for idx, m in enumerate(chat["messages"]):
    _render(m, latest=idx == len(chat["messages"]) - 1)

pending = None
if not chat["messages"]:
    st.caption("Ask a question or pick a suggestion below.")
    cols = st.columns(3)
    for col, text in zip(cols, SUGGESTIONS):
        if col.button(text, key=f"sug_{text}", use_container_width=True):
            pending = text

typed = st.chat_input("Ask about your invoices")
question = typed or pending
if question:
    if chat["title"] == "New chat":
        chat["title"] = question[:40]
    chat["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking…"):
                reply = api_client.ask(question, st.session_state.current_chat, scope_id)
            message = {"role": "assistant", "content": reply.get("answer") or "I could not find an answer.",
                       "sources": reply.get("sources") or [], "cache_hit": bool(reply.get("cache_hit")),
                       "ragas_metrics": reply.get("ragas_metrics") or {}, "eval_id": reply.get("eval_id")}
        except ApiError as exc:
            message = {"role": "assistant",
                       "content": f"Sorry, I couldn't get an answer just now. {exc} Please try again."}
        st.markdown(message["content"])
    chat["messages"].append(message)
    st.rerun()
