from __future__ import annotations

import pandas as pd

from app.formatting import checks_text, money, outcome_text, pretty_date, pretty_datetime
from app.models import Invoice


def to_frame(items: list[Invoice]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Invoice no.": i.invoice_number or "(not found)",
            "Vendor": i.vendor_name or "(unknown)",
            "Date": pretty_date(i.invoice_date),
            "Currency": i.currency or "—",
            "Total": money(i.total_amount),
            "Checks": checks_text(i.validation_status) if i.validation_status else "🔄 Processing",
            "Outcome": outcome_text(i),
            "Language": (i.source_language or "—").upper(),
            "Confidence": None if i.translation_confidence is None
            else round(i.translation_confidence * 100),
            "Processed": pretty_datetime(i.processed_at),
        }
        for i in items
    ])


def picker_label(i: Invoice) -> str:
    return f"{i.invoice_number or '(no number)'} · {i.vendor_name or 'unknown vendor'} · " \
           f"{outcome_text(i)}"

