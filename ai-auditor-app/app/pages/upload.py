import streamlit as st

from app import api_client, ui
from app.api_client import ApiError
from app.config import ALLOWED_EXTENSIONS, MAX_FILE_BYTES

SIGNATURES = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04",),
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def validate(name: str, data: bytes) -> str | None:
    """Returns an error message, or None if the file is acceptable."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if len(data) == 0:
        return "The file is empty."
    if len(data) > MAX_FILE_BYTES:
        return "The file is larger than 10 MB."
    if ext not in SIGNATURES:
        return "Unsupported file type. Use PDF, DOCX, PNG, JPG or JPEG."
    if not data.startswith(SIGNATURES[ext]):
        return f"The content does not look like a real .{ext} file."
    return None


ui.header("WORKSPACE / UPLOAD", "Every audit starts here.",
          "Add your invoices in one place. Keep your documents and their status together.")
st.info("Your files are saved on the server and picked up by the audit agent within seconds. It reads each "
        "document (using OCR for scans), translates it to English if needed, checks it against the ERP, and "
        "writes a report. Follow progress on the Dashboard.")

left, right = st.columns([2.2, 1], gap="large")

with left:
    with st.container(border=True):
        st.subheader("Upload invoices")
        st.caption("PDF, DOCX, PNG, JPG, JPEG / Up to 10 MB per file")
        files = st.file_uploader("Choose invoices", type=ALLOWED_EXTENSIONS, accept_multiple_files=True,
                                 key=f"uploader_{st.session_state.get('uploader_gen', 0)}")
        st.caption("Remove a selected file using its X control before submitting.")
        submit = st.button("Add to workspace", type="primary", icon=":material/upload:",
                           use_container_width=True, disabled=not files)

    if submit and files:
        valid, errors = [], []
        for f in files:
            data = f.getvalue()
            problem = validate(f.name, data)
            if problem:
                errors.append((f.name, problem))
            else:
                valid.append((f.name, data, MIME[f.name.rsplit(".", 1)[-1].lower()]))
        for name, problem in errors:
            st.error(f"**{name}** was not uploaded. {problem}")
        if valid:
            try:
                with st.spinner(f"Uploading {len(valid)} file(s)…"):
                    results = api_client.upload(valid)
            except ApiError as exc:
                st.error(f"Upload failed. {exc}")
            else:
                for r in results:
                    status = r["status"]
                    if status == "QUEUED":
                        st.success(f"**{r['filename']}** was added. It will appear on the Dashboard while it is processed.")
                    elif status == "DUPLICATE":
                        st.info(f"**{r['filename']}** is already in your workspace, so it was not added again.")
                    else:
                        st.error(f"**{r['filename']}** was rejected. {r.get('reason') or 'The file could not be accepted.'}")
                st.cache_data.clear()
                if any(r["status"] == "QUEUED" for r in results) and not errors:
                    st.session_state["uploader_gen"] = st.session_state.get("uploader_gen", 0) + 1
                    st.session_state["upload_notice"] = [r for r in results]
                    st.rerun()

    notice = st.session_state.pop("upload_notice", None)
    if notice:
        for r in notice:
            if r["status"] == "QUEUED":
                st.success(f"**{r['filename']}** was added. It will appear on the Dashboard while it is processed.")
            elif r["status"] == "DUPLICATE":
                st.info(f"**{r['filename']}** is already in your workspace, so it was not added again.")
            else:
                st.error(f"**{r['filename']}** was rejected. {r.get('reason') or ''}")
        st.page_link("pages/dashboard.py", label="Go to Dashboard", icon=":material/dashboard:")

with right:
    with st.container(border=True):
        st.subheader("A cleaner invoice, a better review")
        st.markdown("**01 / Keep it readable**")
        st.caption("Use clear scans with the full document visible.")
        st.markdown("**02 / One invoice per file**")
        st.caption("Keep multi-page invoices together in a single PDF.")
        st.markdown("**03 / Follow its status**")
        st.caption("Open the Dashboard to find your document and its report.")
        st.divider()
        st.caption("What happens next")
        st.caption("Upload → Queue → Extract → Audit → Report ready")
