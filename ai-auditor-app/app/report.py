"""Contents of the 'Invoice report' dialog."""
import base64

import streamlit as st
import streamlit.components.v1 as components

from app import api_client, ui
from app.api_client import ApiError


@st.cache_data(ttl=60, show_spinner=False)
def _original(document_id: str) -> bytes:
    return api_client.get_original(document_id)


def _document_preview(doc: dict, document_id: str) -> None:
    ext = (doc.get("file_type") or "").lower()
    try:
        data = _original(document_id)
    except ApiError:
        st.warning("The original document is not available.")
        return
    mime = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(
        ext, "application/octet-stream")
    if ext in ("png", "jpg", "jpeg"):
        st.image(data, use_container_width=True)
    elif ext == "pdf":
        b64 = base64.b64encode(data).decode()
        components.html(f'<iframe src="data:application/pdf;base64,{b64}" width="100%" height="500" '
                        'style="border:1px solid #E3E9EE;border-radius:10px"></iframe>', height=510)
    elif ext == "docx":
        st.info("Inline preview is not available for DOCX files. Use Download original to open it.")
    st.download_button("Download original", data=data, file_name=doc.get("filename") or "invoice",
                       mime=mime, icon=":material/download:")


def render_report(document_id: str, close) -> None:
    with st.spinner("Loading report…"):
        try:
            doc = api_client.get_document(document_id)
        except ApiError as exc:
            if exc.kind == "not_found":
                st.error("This invoice could not be found. It may have been removed.")
            else:
                st.error(f"We couldn't load this report. {exc}")
            if st.button("Dismiss", use_container_width=True):
                close()
                st.rerun()
            return

    st.subheader(ui.display_name(doc))
    if doc.get("vendor_name"):
        st.write(doc["vendor_name"])
    else:
        st.caption("Vendor will appear once extraction finishes.")

    m1, m2 = st.columns(2)
    m1.metric("Invoice amount", ui.fmt_amount(doc))
    m2.metric("Processing status", ui.PROCESSING_LABELS.get(doc["processing_status"], "Processing"))

    r1, r2 = st.columns(2)
    with r1:
        st.html('<p class="label">AUDIT RESULT</p>')
        ui.audit_badge(doc["audit_result"])
    with r2:
        ui.labelled("UPLOADED", ui.fmt_timestamp(doc["uploaded_at"]))

    st.divider()
    st.subheader("Audit findings")
    status, result = doc["processing_status"], doc["audit_result"]
    if status == "audited" and result == "no_issues":
        st.success("Audited with no issues. Everything matched the purchase order.")
    elif status == "audited":
        st.warning("This invoice needs attention.")
        for reason in doc.get("reasons") or []:
            st.warning(reason)
        for finding in doc.get("findings") or []:
            st.warning(finding["message"])
    elif status == "failed":
        st.error(doc.get("error") or "Processing failed for this document.")
    elif status == "awaiting":
        st.info("Waiting for the audit backend to pick up this document.")
    else:
        st.info("This invoice is still being processed. Check back in a moment.")

    st.subheader("Original document")
    _document_preview(doc, document_id)

    st.divider()
    st.caption("Have questions about this invoice? Ask them on the Invoice Assistant page.")
    if st.button("Close report", use_container_width=True):
        close()
        st.rerun()
