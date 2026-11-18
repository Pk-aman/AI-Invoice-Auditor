"""Altair charts with colour-blind-safe colours and takeaway titles."""
from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from app import ui_text as T
from app.formatting import field_label
from app.models import DayStat, DiscrepancyTypeStat, Summary, VendorStat


def _empty() -> None:
    st.caption(T.NO_CHART_DATA)


def by_recommendation(s: Summary) -> None:
    rows = [(T.RECOMMENDATION[k][0], s.by_recommendation.get(k, 0)) for k in T.RECOMMENDATION]
    df = pd.DataFrame(rows, columns=["Outcome", "Invoices"])
    if df["Invoices"].sum() == 0:
        st.markdown(f"**{T.CHART_REC_PLAIN}**")
        return _empty()
    top = df.sort_values("Invoices", ascending=False).iloc[0]["Outcome"].lower()
    colors = alt.Scale(domain=[T.RECOMMENDATION[k][0] for k in T.RECOMMENDATION],
                       range=[T.COLOR_APPROVED, T.COLOR_REVIEW, T.COLOR_REJECTED])
    base = alt.Chart(df, title=T.CHART_REC.format(top=top)).encode(
        x=alt.X("Outcome:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("Invoices:Q", title="Invoices"))
    chart = base.mark_bar().encode(color=alt.Color("Outcome:N", scale=colors, legend=None)) + \
        base.mark_text(dy=-8).encode(text="Invoices:Q")
    st.altair_chart(chart, width="stretch")


def over_time(days: list[DayStat]) -> None:
    if not days:
        st.markdown(f"**{T.CHART_TIME}**")
        return _empty()
    df = pd.DataFrame([{"Day": d.day, "Invoices": d.count} for d in days])
    chart = alt.Chart(df, title=T.CHART_TIME).mark_line(point=True, color=T.COLOR_NEUTRAL).encode(
        x=alt.X("Day:T", title=None), y=alt.Y("Invoices:Q", title="Invoices"),
        tooltip=["Day:T", "Invoices:Q"])
    st.altair_chart(chart, width="stretch")


def vendors(stats: list[VendorStat]) -> None:
    stats = [v for v in stats if v.discrepancies > 0]
    if not stats:
        st.markdown(f"**{T.CHART_VENDOR_PLAIN}**")
        return _empty()
    df = pd.DataFrame([{"Vendor": v.vendor, "Discrepancies": v.discrepancies} for v in stats])
    df = df.sort_values("Discrepancies", ascending=False).head(8)
    title = T.CHART_VENDOR.format(vendor=df.iloc[0]["Vendor"], n=int(df.iloc[0]["Discrepancies"]))
    chart = alt.Chart(df, title=title).mark_bar(color=T.COLOR_NEUTRAL).encode(
        y=alt.Y("Vendor:N", sort="-x", title=None), x=alt.X("Discrepancies:Q"),
        tooltip=["Vendor:N", "Discrepancies:Q"])
    st.altair_chart(chart, width="stretch")


def discrepancy_types(stats: list[DiscrepancyTypeStat]) -> None:
    stats = [s for s in stats if s.count > 0]
    if not stats:
        st.markdown(f"**{T.CHART_TYPES_PLAIN}**")
        return _empty()
    df = pd.DataFrame([{"Problem": field_label(s.field_name), "Count": s.count} for s in stats])
    df = df.groupby("Problem", as_index=False)["Count"].sum().sort_values("Count", ascending=False)
    df = df.head(8)
    title = T.CHART_TYPES.format(field=df.iloc[0]["Problem"].lower(), n=int(df.iloc[0]["Count"]))
    chart = alt.Chart(df, title=title).mark_bar(color=T.COLOR_REVIEW).encode(
        y=alt.Y("Problem:N", sort="-x", title=None), x=alt.X("Count:Q"),
        tooltip=["Problem:N", "Count:Q"])
    st.altair_chart(chart, width="stretch")
