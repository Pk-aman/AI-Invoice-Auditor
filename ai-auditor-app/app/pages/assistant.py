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
    data = api_client.list_documents({"processing_status": "audited", "page_size": 100})
    return [d for d in data["items"] if d.get("invoice_id")]


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


def _render(message: dict) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("sources"):
            with st.expander("Sources"):
                for s in message["sources"]:
                    st.caption(f"{s.get('invoice_number') or s.get('invoice_id') or 'Database'} · "
                               f"{s.get('source_type', '')}")
                    if s.get("excerpt"):
                        st.write(s["excerpt"])


for m in chat["messages"]:
    _render(m)

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
                       "sources": reply.get("sources") or []}
        except ApiError as exc:
            message = {"role": "assistant",
                       "content": f"Sorry, I couldn't get an answer just now. {exc} Please try again."}
        st.markdown(message["content"])
    chat["messages"].append(message)
    st.rerun()
