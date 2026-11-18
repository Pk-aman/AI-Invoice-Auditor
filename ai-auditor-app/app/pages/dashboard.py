import streamlit as st

from app import api_client, ui
from app.api_client import ApiError
from app.config import PAGE_SIZE

STATUS_OPTIONS = {"All statuses": None, "Audited": "audited", "Processing": "processing",
                  "Failed": "failed", "Awaiting backend": "awaiting"}
RESULT_OPTIONS = {"All results": None, "No issues": "no_issues", "Needs attention": "needs_attention",
                  "Not available": "not_available"}
DATE_OPTIONS = {"All time": None, "Last 7 days": 7, "Last 30 days": 30}


@st.cache_data(ttl=5, show_spinner=False)
def _stats() -> dict:
    return api_client.document_stats()


@st.cache_data(ttl=5, show_spinner=False)
def _documents(params: tuple) -> dict:
    return api_client.list_documents(dict(params))


def _open_report(document_id: str) -> None:
    st.query_params["invoice"] = document_id


def _close_report() -> None:
    st.query_params.pop("invoice", None)


@st.dialog("Invoice report", width="large", on_dismiss=_close_report)
def report_dialog(document_id: str) -> None:
    from app.report import render_report

    render_report(document_id, close=_close_report)


ui.header("WORKSPACE / OVERVIEW", "Your invoices, at a glance.",
          "Track every document. Spot issues early. Keep your review moving.")
if st.button("Upload invoice", type="primary", icon=":material/add:"):
    st.switch_page("pages/upload.py")

try:
    stats = _stats()
except ApiError as exc:
    st.error(f"We couldn't load your invoices. {exc}")
    if st.button("Try again", icon=":material/refresh:"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

cols = st.columns(4)
cols[0].metric("Total invoices", stats["total"])
cols[1].metric("Processing", stats["processing"])
cols[2].metric("Audited", stats["audited"])
cols[3].metric("Needs attention", stats["needs_attention"])
st.caption(f"{stats['awaiting']} awaiting backend connection | {stats['failed']} processing failures")

with st.container(border=True):
    st.subheader("Invoice workspace")
    st.caption("Find an invoice and open its report. All dates are shown in UTC.")
    f1, f2, f3, f4 = st.columns([2.2, 1.3, 1.4, 1.2])
    query = f1.text_input("Search invoices", placeholder="Invoice number, filename, or vendor").strip()
    status = f2.selectbox("Processing status", list(STATUS_OPTIONS))
    result = f3.selectbox("Audit result", list(RESULT_OPTIONS))
    since = f4.selectbox("Upload date", list(DATE_OPTIONS))

    signature = (query, status, result, since)
    if st.session_state.get("filter_signature") != signature:
        st.session_state["filter_signature"] = signature
        st.session_state["results_page"] = 1

    params = {"page_size": PAGE_SIZE, "page": int(st.session_state.get("results_page", 1))}
    if query:
        params["q"] = query
    if STATUS_OPTIONS[status]:
        params["processing_status"] = STATUS_OPTIONS[status]
    if RESULT_OPTIONS[result]:
        params["audit_result"] = RESULT_OPTIONS[result]
    if DATE_OPTIONS[since]:
        params["uploaded_within_days"] = DATE_OPTIONS[since]

    try:
        data = _documents(tuple(sorted(params.items())))
    except ApiError as exc:
        st.error(f"We couldn't retrieve invoices. {exc}")
        if st.button("Retry", key="retry_list", icon=":material/refresh:"):
            st.cache_data.clear()
            st.rerun()
        data = None

    if data is not None:
        total = data["total"]
        pages = max(1, -(-total // PAGE_SIZE))
        st.selectbox("Results page", list(range(1, pages + 1)), key="results_page")
        if int(st.session_state["results_page"]) != params["page"]:
            st.rerun()
        if total == 0:
            filtered = any(v is not None for v in (query or None, STATUS_OPTIONS[status],
                                                    RESULT_OPTIONS[result], DATE_OPTIONS[since]))
            if filtered:
                st.info("No invoices match these filters. Try widening your search.")
            else:
                st.info("No invoices yet. Upload your first invoice to start an audit.")
        else:
            first = (params["page"] - 1) * PAGE_SIZE + 1
            st.caption(f"Showing {first}–{first + len(data['items']) - 1} of {total} invoices")
            for doc in data["items"]:
                st.divider()
                c1, c2, c3, c4, c5 = st.columns([2.5, 1.2, 1.2, 1.3, 1], vertical_alignment="center")
                with c1:
                    st.markdown(f"**{ui.display_name(doc)}**")
                    st.caption(doc.get("vendor_name") or "Vendor not extracted")
                    st.caption(ui.fmt_date(doc["uploaded_at"]))
                with c2:
                    ui.labelled("AMOUNT", ui.fmt_amount(doc))
                with c3:
                    ui.labelled("PROCESSING", ui.PROCESSING_LABELS.get(doc["processing_status"], "Processing"))
                with c4:
                    st.html('<p class="label">AUDIT RESULT</p>')
                    ui.audit_badge(doc["audit_result"])
                with c5:
                    st.button("View report", key=f"view_{doc['document_id']}", on_click=_open_report,
                              args=(doc["document_id"],), use_container_width=True)


@st.fragment(run_every=10)
def _watch_for_changes() -> None:
    """Refresh the page when documents change state (e.g. processing -> audited), unless a report is open."""
    try:
        latest = api_client.document_stats()
    except ApiError:
        return
    seen = st.session_state.get("stats_seen")
    st.session_state["stats_seen"] = latest
    if seen is not None and seen != latest and "invoice" not in st.query_params:
        st.cache_data.clear()
        st.rerun(scope="app")


_watch_for_changes()

if "invoice" in st.query_params:
    report_dialog(st.query_params["invoice"])
