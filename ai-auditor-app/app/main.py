import streamlit as st

from app import api_client, ui

st.set_page_config(page_title="Invoice Monitor", page_icon=":material/receipt_long:", layout="wide")
ui.apply_theme()

dashboard = st.Page("pages/dashboard.py", title="Dashboard", icon=":material/dashboard:", default=True)
upload = st.Page("pages/upload.py", title="Upload Invoice", icon=":material/upload_file:", url_path="upload")
assistant = st.Page("pages/assistant.py", title="Invoice Assistant", icon=":material/chat:", url_path="assistant")
nav = st.navigation([dashboard, upload, assistant], position="hidden")

try:
    total = api_client.document_stats()["total"]
    connected = True
except api_client.ApiError:
    total, connected = None, False

with st.sidebar:
    st.markdown("### :material/receipt_long: Invoice Monitor")
    st.caption("Clarity in every invoice.")
    st.page_link(dashboard, label="Dashboard", icon=":material/dashboard:")
    st.page_link(upload, label="Upload Invoice", icon=":material/upload_file:")
    st.page_link(assistant, label="Invoice Assistant", icon=":material/chat:")
    st.divider()
    if connected:
        st.markdown(f"**{total}** invoice(s) in workspace")
        st.caption("Connected to the audit backend.")
    else:
        st.markdown("**–** invoice(s) in workspace")
        st.caption("Audit backend unavailable. Showing no data until it reconnects.")

nav.run()
