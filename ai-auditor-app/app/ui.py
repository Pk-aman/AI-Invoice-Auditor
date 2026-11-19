"""Theme, shared helpers and small display components."""
from __future__ import annotations

from datetime import datetime

import streamlit as st

CSS = """
<style>
.stApp { background:#F6F8FA; color:#172B3A; font-family:"Inter","Segoe UI",system-ui,-apple-system,sans-serif; }
.block-container { max-width:1440px; padding-top:2.4rem; }
@media (max-width:640px){ .block-container{ padding:1.2rem 1rem; } }
h1 { font-weight:750 !important; letter-spacing:-0.045em !important; color:#172B3A; }
h2,h3,h4 { letter-spacing:-0.025em !important; color:#172B3A; }
[data-testid="stCaptionContainer"], .stCaption { color:#526575; }
[data-testid="stSidebar"] { background:#FFFFFF; border-right:1px solid #E3E9EE; }
[data-testid="stMetric"] { background:#FFFFFF; border:1px solid #E3E9EE; border-radius:14px; padding:20px; min-height:120px; }
[data-testid="stVerticalBlockBorderWrapper"] { border-color:#E3E9EE; background:#FFFFFF; border-radius:14px; }
.stButton > button, .stDownloadButton > button { border-radius:9px; }
.stButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] { background:#087F73; border-color:#087F73; color:#fff; }
.stButton > button[kind="primary"]:hover { background:#066b61; border-color:#066b61; }
[data-testid="stFileUploader"] section { background:#FFFFFF; border:1px solid #E3E9EE; border-radius:14px; padding:16px 20px; }
.eyebrow { color:#087F73; font-size:0.78rem; font-weight:650; letter-spacing:0.12em; text-transform:uppercase; margin-bottom:0.2rem; }
.label { color:#526575; font-size:0.72rem; font-weight:650; letter-spacing:0.1em; text-transform:uppercase; margin:0; }
.cell { color:#172B3A; font-size:0.98rem; margin:0.1rem 0 0 0; }
.muted { color:#526575; }
</style>
"""

PROCESSING_LABELS = {"audited": "Audited", "review": "Awaiting review", "processing": "Processing", "failed": "Failed",
                     "awaiting": "Awaiting backend", "duplicate": "Duplicate"}
AUDIT_LABELS = {"no_issues": "No issues", "needs_attention": "Needs attention", "approved": "Approved", "rejected": "Rejected",
                "not_available": "Not available"}


def apply_theme() -> None:
    st.html(CSS)


def header(eyebrow: str, title: str, description: str) -> None:
    st.html(f'<div class="eyebrow">{eyebrow}</div>')
    st.title(title)
    st.write(description)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_date(value: str | None) -> str:
    d = parse_ts(value)
    return d.strftime("%d %b %Y UTC") if d else "Date unavailable"


def fmt_timestamp(value: str | None) -> str:
    d = parse_ts(value)
    return d.strftime("%d %b %Y, %H:%M UTC") if d else "Unavailable"


def fmt_amount(doc: dict) -> str:
    amount = doc.get("total_amount")
    if amount in (None, ""):
        return "Not extracted"
    try:
        text = f"{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "Not extracted"
    return f"{doc['currency']} {text}" if doc.get("currency") else text


def display_name(doc: dict) -> str:
    return doc.get("invoice_number") or doc.get("filename") or "Untitled document"


def audit_badge(result: str) -> None:
    if result == "needs_attention":
        st.badge("Needs attention", icon=":material/flag:", color="orange")
    elif result == "no_issues":
        st.badge("No issues", icon=":material/check_circle:", color="green")
    elif result == "approved":
        st.badge("Approved", icon=":material/verified:", color="green")
    elif result == "rejected":
        st.badge("Rejected", icon=":material/cancel:", color="red")
    else:
        st.markdown('<p class="cell">Not available</p>', unsafe_allow_html=True)


def labelled(label: str, value: str) -> None:
    st.html(f'<p class="label">{label}</p><p class="cell">{value}</p>')
