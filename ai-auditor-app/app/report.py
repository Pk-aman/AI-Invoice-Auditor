"""Contents of the 'Invoice report' dialog: the immutable AI report, the live review state, and the human review form."""
import pandas as pd
import streamlit as st

from app import api_client, review_logic, ui
from app.api_client import ApiError


MAX_PREVIEW_PAGES = 10


@st.cache_data(ttl=300, show_spinner=False)
def pdf_pages(data: bytes, dpi: int = 110) -> tuple[list[bytes], int]:
    """Render PDF pages to PNG with PyMuPDF. Browsers (Chrome) block PDFs embedded from data: URLs, so we show images."""
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as doc:
        total = doc.page_count
        return [doc[i].get_pixmap(dpi=dpi, alpha=False).tobytes("png") for i in range(min(total, MAX_PREVIEW_PAGES))], total


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
        try:
            pages, total = pdf_pages(data)
        except Exception:  # noqa: BLE001 - corrupt/encrypted PDF: the download button below still works
            st.warning("A preview could not be created for this PDF. Use Download original to open it.")
        else:
            for n, png in enumerate(pages, start=1):
                st.image(png, caption=f"Page {n} of {total}", use_container_width=True)
            if total > len(pages):
                st.caption(f"Showing the first {len(pages)} of {total} pages. Download the original to see the rest.")
    elif ext == "docx":
        st.info("Inline preview is not available for DOCX files. Use Download original to open it.")
    st.download_button("Download original", data=data, file_name=doc.get("filename") or "invoice",
                       mime=mime, icon=":material/download:")


# ---- AI report (read-only) -----------------------------------------------------------------------------------
def _show_ai_report(report: dict | None, *, heading: str) -> None:
    """Render a report snapshot. Read-only by construction: there is no input widget anywhere in here."""
    if not report:
        st.info("This report is not available yet.")
        return
    st.markdown(f"**{heading}**")
    cols = st.columns(3)
    cols[0].metric("Recommendation", report.get("recommendation_label") or str(report.get("recommendation", "")).title())
    cols[1].metric("Validation", str(report.get("validation_status", "")).replace("_", " ").title())
    cols[2].metric("Validation run", report.get("validation_run", 1))
    if report.get("summary"):
        st.write(report["summary"])
    for reason in report.get("reasons") or []:
        st.caption(f"• {reason}")
    missing = report.get("missing_fields") or []
    if missing:
        st.warning("Missing fields: " + ", ".join(str(m) for m in missing))
    discrepancies = report.get("discrepancies") or []
    if discrepancies:
        st.markdown("**Differences from the purchase order / ERP**")
        st.dataframe(pd.DataFrame([{
            "Field": d.get("field_name"), "On the invoice": d.get("invoice_value"), "In the ERP": d.get("erp_value"),
            "Difference %": d.get("deviation_pct"), "Severity": d.get("severity")} for d in discrepancies]),
            hide_index=True, use_container_width=True)
    findings = report.get("data_findings") or []
    if findings:
        st.markdown("**Data checks**")
        for f in findings:
            st.caption(f"[{f.get('severity', 'medium')}] {f.get('message', '')}")
    if report.get("corrections"):
        st.caption("Reviewer corrections applied in this run: " + ", ".join(f"{k} → {v}" for k, v in report["corrections"].items()))


# ---- review form -----------------------------------------------------------------------------------------------
def _review_form(iid: str, state: dict) -> None:
    run = state.get("validation_run") or 1
    invoice = state.get("invoice") or {}
    st.markdown("#### Your review")
    level, text = review_logic.checks_message(state)
    (st.warning if level == "warning" else st.info)(text)
    for w in state.get("warnings") or []:
        st.warning(w)

    with st.form(f"review_{iid}_{run}", border=True):
        st.caption("Fix any value the system read wrongly, then choose Reject & revalidate. "
                   "If everything is correct, choose Approve.")
        header: dict[str, str] = {}
        cols = st.columns(2)
        for i, (field, label) in enumerate(review_logic.HEADER_FIELDS):
            header[field] = cols[i % 2].text_input(label, value="" if invoice.get(field) is None else str(invoice[field]),
                                                   key=f"h_{field}_{iid}_{run}")
        lines_df = pd.DataFrame([{c: ("" if ln.get(c) is None else str(ln[c])) for c, _ in review_logic.LINE_COLUMNS}
                                 for ln in invoice.get("line_items") or []],
                                columns=[c for c, _ in review_logic.LINE_COLUMNS])
        edited = lines_df
        if len(lines_df):
            st.markdown("**Line items**")
            edited = st.data_editor(
                lines_df, num_rows="fixed", hide_index=True, use_container_width=True, key=f"lines_{iid}_{run}",
                column_config={c: st.column_config.TextColumn(label) for c, label in review_logic.LINE_COLUMNS})
        reviewer = st.text_input("Your name", value=st.session_state.get("reviewer_name", ""), key=f"who_{iid}_{run}",
                                 help="Recorded with your decision and corrections.")
        comments = st.text_area("Comments (optional)", key=f"why_{iid}_{run}", max_chars=2000)
        b1, b2, b3 = st.columns(3)
        approve = b1.form_submit_button("Approve", type="primary", icon=":material/check:", use_container_width=True)
        revalidate = b2.form_submit_button("Reject & revalidate", icon=":material/refresh:", use_container_width=True)
        reject = b3.form_submit_button("Reject", icon=":material/close:", use_container_width=True)

    decision = "approve" if approve else "reject_and_revalidate" if revalidate else "reject" if reject else None
    if decision is None:
        return
    corrections = review_logic.diff_corrections(invoice, header, edited.to_dict("records") if len(edited) else [])
    if decision == "reject":
        corrections = []  # a plain rejection records the reviewer's notes, not field edits
    problem = review_logic.validate_submission(decision, reviewer, corrections)
    if problem:
        st.error(problem)
        return
    st.session_state["reviewer_name"] = reviewer.strip()
    body = {"decision": decision, "reviewer": reviewer.strip(), "comments": comments.strip() or None,
            "field_corrections": [{"field_name": c["field_name"], "original_value": c.get("original_value"),
                                   "corrected_value": c["corrected_value"]} for c in corrections]}
    try:
        with st.spinner("Saving your review…" if decision != "reject_and_revalidate" else "Saving your corrections and re-validating…"):
            resp = api_client.submit_review(iid, body)
    except ApiError as exc:
        if exc.kind == "unreachable":
            st.error("The audit agent could not be reached, so nothing was saved. Please try again.")
        else:
            st.error(f"Your review was not saved. {exc}")
            if "retry" in str(exc).lower():
                if st.button("Resume this invoice", key=f"retry_{iid}", icon=":material/restart_alt:"):
                    _retry(iid)
        return
    level, message = review_logic.response_notice(resp)
    st.session_state["review_notice"] = (level, message, resp.get("warnings") or [])
    st.cache_data.clear()
    st.rerun()


def _retry(iid: str) -> None:
    try:
        api_client.retry_invoice(iid)
    except ApiError as exc:
        st.error(f"Could not resume: {exc}")
    else:
        st.success("Processing was resumed. Reopen this report in a moment.")


def _history(iid: str) -> None:
    try:
        rows = api_client.review_history(iid)
    except ApiError:
        return
    if rows:
        st.markdown("**Review history**")
        st.dataframe(pd.DataFrame([{"Round": r["review_round"], "Decision": review_logic.DECISION_LABEL.get(r["decision"], r["decision"]),
                                    "Reviewer": r["reviewer"], "Comments": r.get("comments") or ""} for r in rows]),
                     hide_index=True, use_container_width=True)


def _review_section(doc: dict, document_id: str) -> None:
    invoice_id = doc.get("invoice_id")
    if not invoice_id:
        return
    iid = str(invoice_id)
    try:
        state = api_client.review_state(iid)
    except ApiError as exc:
        st.caption(f"Review controls are unavailable right now. {exc}")
        return

    st.divider()
    st.subheader("Review")
    notice = st.session_state.pop("review_notice", None)
    if notice:
        level, message, warnings = notice
        {"success": st.success, "warning": st.warning}.get(level, st.info)(message)
        for w in warnings:
            st.warning(w)
    status = state.get("status", "")
    st.caption(f"Status: **{review_logic.STATUS_TEXT.get(status, status)}** · Recommendation: **{state.get('recommendation') or '—'}** · "
               f"Validation: **{str(state.get('validation_status') or '—').replace('_', ' ')}**")

    tabs = st.tabs(["Latest validation", "Original AI report (read-only)"]) if (state.get("validation_run") or 1) > 1 \
        else st.tabs(["AI report (read-only)"])
    with tabs[0]:
        _show_ai_report(state.get("report"), heading="Audit report")
    if len(tabs) > 1:
        with tabs[1]:
            _show_ai_report(state.get("ai_audit_report"), heading="Original AI report")
            st.caption("The original AI report is stored as an immutable snapshot. Your corrections never change it.")

    if state.get("awaiting_review"):
        _review_form(iid, state)
    elif status == "PROCESSING":
        st.info("The invoice is still being processed. Review will be available when the audit is ready.")
        if st.button("Resume stalled processing", key=f"resume_{iid}", icon=":material/restart_alt:"):
            _retry(iid)
    else:
        level, text = review_logic.checks_message(state)
        st.caption(text)
        if state.get("corrections"):
            st.caption("Corrections recorded: " + ", ".join(f"{k} → {v}" for k, v in state["corrections"].items()))
    _history(iid)


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
    if status == "duplicate":
        st.info("This document has the same content as an invoice that was already audited, so it was not processed again.")
    elif status in ("audited", "review") and result == "no_issues":
        st.success("No issues found. Everything matched the purchase order.")
    elif status in ("audited", "review"):
        if result in ("approved", "rejected"):
            st.info(f"A reviewer {result} this invoice.")
        else:
            st.warning("This invoice needs attention." if status == "audited" else "The audit found points to check before you decide.")
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

    if status in ("audited", "review", "processing"):
        _review_section(doc, document_id)

    st.subheader("Original document")
    _document_preview(doc, document_id)

    st.divider()
    st.caption("Have questions about this invoice? Ask them on the Invoice Assistant page.")
    if st.button("Close report", use_container_width=True):
        close()
        st.rerun()
