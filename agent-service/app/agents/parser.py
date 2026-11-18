"""Parser agent: translated text -> typed ExtractedInvoice (schema-guided LLM JSON + regex fallbacks)."""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from app import prompts
from app.core.llm import LLM, LLMError, extract_json
from app.core.normalize import normalize_currency, parse_amount, parse_date
from app.core.rules import Rules
from app.schemas.models import ExtractedInvoice, LineItem

log = logging.getLogger(__name__)

# accept both rules.yaml names and DB names from the model's JSON
_HEADER_KEYS = {
    "invoice_number": ("invoice_number", "invoice_no", "invoice_id"),
    "vendor_name": ("vendor_name", "vendor", "supplier"),
    "vendor_id": ("vendor_id",),
    "po_number": ("po_number", "po_no", "purchase_order"),
    "invoice_date": ("invoice_date", "date"),
    "currency": ("currency",),
    "subtotal": ("subtotal", "sub_total"),
    "tax_amount": ("tax_amount", "tax", "vat"),
    "tax_rate": ("tax_rate",),
    "total_amount": ("total_amount", "total", "grand_total"),
}
_LINE_KEYS = {
    "item_code": ("item_code", "sku", "code"),
    "description": ("description", "desc"),
    "quantity": ("quantity", "qty"),
    "unit_price": ("unit_price", "price", "rate"),
    "line_total": ("line_total", "total", "amount"),
}


def _pick(d: dict, names: tuple[str, ...]):
    for n in names:
        if d.get(n) not in (None, ""):
            return d[n]
    return None


def _str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def build_invoice(data: dict, rules: Rules) -> ExtractedInvoice:
    h = {k: _pick(data, names) for k, names in _HEADER_KEYS.items()}
    lines: list[LineItem] = []
    for i, raw in enumerate(data.get("line_items") or [], start=1):
        if not isinstance(raw, dict):
            continue
        li = {k: _pick(raw, names) for k, names in _LINE_KEYS.items()}
        lines.append(LineItem(
            line_number=i, item_code=_str(li["item_code"]), description=_str(li["description"]),
            quantity=parse_amount(li["quantity"]), unit_price=parse_amount(li["unit_price"]),
            line_total=parse_amount(li["line_total"]),
        ))
    extras = data.get("extras") if isinstance(data.get("extras"), dict) else {}
    return ExtractedInvoice(
        invoice_number=_str(h["invoice_number"]), vendor_name=_str(h["vendor_name"]), vendor_id=_str(h["vendor_id"]),
        po_number=_str(h["po_number"]), invoice_date=parse_date(h["invoice_date"]),
        currency=normalize_currency(h["currency"], rules.currency_symbol_map),
        subtotal=parse_amount(h["subtotal"]), tax_amount=parse_amount(h["tax_amount"]),
        tax_rate=parse_amount(h["tax_rate"]), total_amount=parse_amount(h["total_amount"]),
        line_items=lines, extras=extras,
    )


# ---- deterministic fallbacks (only fill what the model left empty) -------------
_INV_NO = re.compile(r"(?:invoice|inv\.?|debit\s*note)\s*(?:no\.?|number|nr\.?|#)?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})", re.I)
_PO_NO = re.compile(r"\b(PO[-\s]?\d{3,})\b", re.I)
_TOTAL = re.compile(r"total(?:\s+amount|\s+due|\s+including\s+tax|\s+incl\.?\s+tax)?\s*[:\-]?\s*[A-Z]{0,3}\s*([\d][\d.,]*)", re.I)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-,]+\d{4})\b")
_CCY = re.compile(r"\b(USD|EUR|INR|GBP|VND|JPY|CNY|AUD|CAD|CHF)\b")


def apply_fallbacks(inv: ExtractedInvoice, text: str, rules: Rules) -> ExtractedInvoice:
    if not inv.invoice_number and (m := _INV_NO.search(text)):
        cand = m.group(1)
        if any(ch.isdigit() for ch in cand):
            inv.invoice_number = cand
    if not inv.po_number and (m := _PO_NO.search(text)):
        inv.po_number = m.group(1).upper().replace(" ", "-")
    if inv.total_amount is None:
        totals = [parse_amount(x) for x in _TOTAL.findall(text)]
        totals = [t for t in totals if t is not None]
        if totals:
            inv.total_amount = max(totals)
    if inv.invoice_date is None:
        for cand in _DATE.findall(text):
            if (d := parse_date(cand)) is not None:
                inv.invoice_date = d
                break
    if not inv.currency:
        for sym, iso in rules.currency_symbol_map.items():
            if sym in text:
                inv.currency = iso
                break
        if not inv.currency and (m := _CCY.search(text)):
            inv.currency = m.group(1)
    return inv


def parse_invoice(translated_text: str, llm: LLM, rules: Rules, *, raw_text: str | None = None) -> ExtractedInvoice:
    cfg = prompts.load("parser")["extract"]
    system = prompts.persona() + "\n\n" + cfg["system"]
    user = prompts.render(cfg["user"], text=translated_text[:12000])
    data: dict | None = None
    for attempt in (1, 2):
        try:
            data = extract_json(llm.complete("reasoning", system, user, json_mode=True, max_tokens=3000))
            break
        except LLMError as exc:
            log.warning("parser attempt failed", extra={"attempt": attempt, "error": str(exc)})
            last_error = exc
    if data is None:
        # No usable model output: continue with deterministic regex extraction only (partial data is flagged downstream)
        log.error("parser LLM failed twice; using regex fallbacks only", extra={"error": str(last_error)})
        data = {}
    inv = build_invoice(data, rules)
    inv = apply_fallbacks(inv, translated_text + "\n" + (raw_text or ""), rules)
    for li in inv.line_items:  # keep money as Decimal with sane precision
        for attr in ("quantity", "unit_price", "line_total"):
            v = getattr(li, attr)
            if isinstance(v, Decimal):
                setattr(li, attr, v)
    return inv
