"""Page 2: Upload Invoices with live progress and duplicate handling."""
from __future__ import annotations

import mimetypes

import streamlit as st

from app import state
from app import ui_text as T
from app.api.http import UiError
from app.api.services_client import get_services
from app.config import get_settings

ALLOWED = ["pdf", "docx", "png", "jpg", "jpeg", "tif", "tiff"]
IMAGE_EXT = {"png", "jpg", "jpeg", "tif", "tiff"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _stage(status: str) -> tuple[str, str, int]:
    return T.UPLOAD_STAGES.get(status.upper(), (status.title(), "•", 0))


def _batch() -> list[dict]:
    return st.session_state.setdefault(state.UPLOAD_BATCH, [])


def _active() -> bool:
    return any(r["status"].upper() not in T.UPLOAD_TERMINAL for r in _batch())


def _poll_once() -> None:
    svc = get_services()
    for row in _batch():
        if row["status"].upper() in T.UPLOAD_TERMINAL or not row.get("checksum"):
            continue
        try:
            s = svc.upload_status(row["checksum"])
        except UiError as err:
            row["note"] = err.message
            continue
        row["status"] = s.status
        row["invoice_id"] = s.invoice_id or row.get("invoice_id")
        if s.error:
            row["note"] = s.error


def _progress_table() -> None:
    batch = _batch()
    if not batch:
        st.caption(T.UPLOAD_PROGRESS_EMPTY)
        return
    if _active():
        _poll_once()
        if not _active():
            st.rerun(scope="app")  # stop the timer once everything is finished
    for i, row in enumerate(batch):
        label, icon, order = _stage(row["status"])
        c = st.columns([3, 3, 4, 2])
        c[0].markdown(f"**{row['name']}**")
        c[1].markdown(f"{icon} {label}")
        terminal = row["status"].upper() in T.UPLOAD_TERMINAL
        c[2].progress(min(order / T.UPLOAD_STAGE_MAX, 1.0),
                      text=row.get("note") or (T.UPLOAD_DUP if row["status"].upper() == "DUPLICATE"
                                               else label))
        done = terminal and row.get("invoice_id") and row["status"].upper() in {"COMPLETED", "DUPLICATE"}
        if done and c[3].button(T.UPLOAD_VIEW, key=f"view_{i}_{row['invoice_id']}"):
            from app.nav import dashboard_page
            state.open_invoice(row["invoice_id"])
            st.switch_page(dashboard_page())
    if not _active():
        st.success(T.UPLOAD_DONE_ALL)


def render() -> None:
    s = get_settings()
    st.title(T.UPLOAD_TITLE)
    st.caption(T.UPLOAD_INTRO)
    files = st.file_uploader(T.UPLOAD_LABEL, type=ALLOWED, accept_multiple_files=True,
                             help=T.UPLOAD_HELP.format(mb=s.max_upload_mb))
    st.caption(T.UPLOAD_HELP.format(mb=s.max_upload_mb))

    valid = []
    for f in files or []:
        size_mb = f.size / (1024 * 1024)
        if _ext(f.name) not in ALLOWED:
            st.warning(T.UPLOAD_BAD_TYPE.format(name=f.name))
        elif size_mb > s.max_upload_mb:
            st.warning(T.UPLOAD_TOO_BIG.format(name=f.name, size=size_mb, mb=s.max_upload_mb))
        else:
            valid.append(f)

    if valid:
        st.subheader(T.UPLOAD_PREVIEW)
        cols = st.columns(min(len(valid), 4))
        for i, f in enumerate(valid):
            with cols[i % len(cols)]:
                if _ext(f.name) in IMAGE_EXT and _ext(f.name) not in {"tif", "tiff"}:
                    st.image(f, caption=f"{f.name} ({f.size / 1024:.0f} KB)", width="stretch")
                else:
                    note = T.UPLOAD_PDF_NOTE if _ext(f.name) == "pdf" else T.UPLOAD_DOC_NOTE
                    st.markdown(f"📄 **{f.name}**  \n{f.size / 1024:.0f} KB · {note}")
    elif files:
        st.error(T.UPLOAD_NONE_VALID)

    if st.button(T.UPLOAD_SUBMIT, type="primary", disabled=not valid):
        payload = [(f.name, f.getvalue(), f.type or mimetypes.guess_type(f.name)[0]
                    or "application/octet-stream") for f in valid]
        try:
            with st.spinner("Sending…"):
                results = get_services().upload(payload)
        except UiError as err:
            st.error(err.message)
        else:
            for r in results:
                _batch().append({"name": r.filename, "checksum": r.checksum, "status": r.status,
                                 "invoice_id": r.invoice_id, "note": r.detail})
            st.toast(T.UPLOAD_SENT.format(n=len(results)), icon="📤")

    st.divider()
    head, clear = st.columns([5, 1])
    head.subheader(T.UPLOAD_PROGRESS)
    if clear.button(T.UPLOAD_CLEAR, disabled=not _batch()):
        st.session_state[state.UPLOAD_BATCH] = []
        st.rerun()
    every = s.poll_upload_s if _active() else None
    st.fragment(run_every=every)(_progress_table)()
