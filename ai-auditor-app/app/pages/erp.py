import pandas as pd
import streamlit as st

from app import api_client, ui
from app.api_client import ApiError

ui.header("REFERENCE / ERP", "ERP master data.",
          "Read-only view of the mock ERP the invoices are validated against.")

try:
    data = api_client.erp_overview()
except ApiError as e:
    st.error(str(e))
    st.stop()

vendors, skus, pos = data["vendors"], data["skus"], data["purchase_orders"]
line_count = sum(len(p["line_items"]) for p in pos)

c = st.columns(3)
c[0].metric("Vendors", len(vendors))
c[1].metric("Purchase orders", f"{len(pos)} ({line_count} lines)")
c[2].metric("SKUs", len(skus))

tab_v, tab_p, tab_s = st.tabs(["Vendors", "Purchase orders", "SKU master"])

with tab_v:
    q = st.text_input("Search vendors", placeholder="Name, ID or country", key="erp_vq")
    df = pd.DataFrame(vendors, columns=["vendor_id", "vendor_name", "country", "currency"])
    if q:
        mask = df.astype(str).apply(lambda col: col.str.contains(q, case=False, regex=False)).any(axis=1)
        df = df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True)

with tab_p:
    names = {v["vendor_id"]: v["vendor_name"] for v in vendors}
    q = st.text_input("Search purchase orders", placeholder="PO number or vendor", key="erp_pq")
    shown = [p for p in pos if not q or q.lower() in p["po_number"].lower()
             or q.lower() in names.get(p["vendor_id"], "").lower() or q.lower() in p["vendor_id"].lower()]
    st.caption(f"{len(shown)} of {len(pos)} purchase orders")
    for p in shown:
        total = sum(ln["qty"] * ln["unit_price"] for ln in p["line_items"])
        cur = p["line_items"][0]["currency"] if p["line_items"] else ""
        with st.expander(f"{p['po_number']} · {names.get(p['vendor_id'], p['vendor_id'])} · {total:,.2f} {cur}"):
            st.dataframe(pd.DataFrame(p["line_items"]), use_container_width=True, hide_index=True)

with tab_s:
    q = st.text_input("Search SKUs", placeholder="Item code or category", key="erp_sq")
    df = pd.DataFrame(skus, columns=["item_code", "category", "uom", "gst_rate"])
    if q:
        mask = df.astype(str).apply(lambda col: col.str.contains(q, case=False, regex=False)).any(axis=1)
        df = df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True)
