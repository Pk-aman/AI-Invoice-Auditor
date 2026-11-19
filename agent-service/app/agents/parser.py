"""Parser agent: translated text -> typed ExtractedInvoice (schema-guided LLM JSON + regex fallbacks)."""
from __future__ import annotations

import difflib
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
_INV_NO_LABELLED = re.compile(r"invoice\s*(?:no\.?|number|nr\.?|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})", re.I)
_PO_NO = re.compile(r"\b(PO[-\s]?\d{3,})\b", re.I)
_TOTAL = re.compile(r"total(?:\s+amount|\s+due|\s+including\s+tax|\s+incl\.?\s+tax)?\s*[:\-]?\s*[A-Z]{0,3}\s*[$€£₹]?\s*([\d][\d.,]*)", re.I)
_SUBTOTAL = re.compile(r"sub\s*-?\s*total\s*[:\-]?\s*[A-Z]{0,3}\s*[$€£₹]?\s*([\d][\d.,]*)", re.I)
_TAX = re.compile(r"(?:^|\n)\s*(?:tax|vat|gst)(?:\s*\(?[\d.]+%\)?)?\s*[:\-]?\s*[A-Z]{0,3}\s*[$€£₹]?\s*([\d][\d.,]*)", re.I)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-,]+\d{4})\b")
_LABELLED_DATE = re.compile(
    r"invoice\s*date\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|"
    r"\d{1,2}[\s\-][A-Za-z]{3,9}[\s\-,]+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})", re.I)
_CCY = re.compile(r"\b(USD|EUR|INR|GBP|VND|JPY|CNY|AUD|CAD|CHF)\b")
_COMPANY = re.compile(r"^(?!.*\b(?:bill\s*to|invoice\s*to|payable\s*to)\b).*\b(?:LLC|LLP|Ltd\.?|Limited|Inc\.?|Corp\.?|GmbH|S\.A\.|S\.L\.|Pvt\.?|Co\.|Company)\b", re.I)
_ROW_FULL = re.compile(  # "1 Desktop furniture 1 $ 232.00 $ 232.00"
    r"^\s*(\d{1,3})\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s+[$€£₹]?\s*([\d.,]+)\s+[$€£₹]?\s*([\d.,]+)\s*$")
_ROW_NUMS = re.compile(  # "1.00 $85.00 0.00% $85.00" (description sits on the line above)
    r"^\s*(\d+(?:[.,]\d+)?)\s+[$€£₹]?\s*([\d.,]+)(?:\s+[\d.,]+\s*%)?\s+[$€£₹]?\s*([\d.,]+)\s*$")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _fix_ocr_month(s: str) -> str:
    """OCR often misreads month abbreviations ('dun 22, 2021'); snap a near-miss to the closest month."""
    def repl(m: re.Match) -> str:
        word = m.group(0)
        if word.lower()[:3] in _MONTHS:
            return word
        close = difflib.get_close_matches(word.lower()[:3], _MONTHS, n=1, cutoff=0.6)
        return close[0].title() if close else word
    return re.sub(r"[A-Za-z]{3,9}", repl, s, count=1)


def _line_items(text: str) -> list[LineItem]:
    lines = [ln.strip() for ln in text.splitlines()]
    items: list[LineItem] = []
    for i, ln in enumerate(lines):
        if m := _ROW_FULL.match(ln):
            qty, rate, amt = parse_amount(m.group(3)), parse_amount(m.group(4)), parse_amount(m.group(5))
            if None in (qty, rate, amt):
                continue
            items.append(LineItem(line_number=len(items) + 1, description=m.group(2).strip(),
                                  quantity=qty, unit_price=rate, line_total=amt))
        elif (m := _ROW_NUMS.match(ln)) and i > 0 and lines[i - 1] and not re.search(r"\d", lines[i - 1][:1]):
            qty, rate, amt = parse_amount(m.group(1)), parse_amount(m.group(2)), parse_amount(m.group(3))
            desc = lines[i - 1]
            if None in (qty, rate, amt) or re.search(r"total|tax|balance|due", desc, re.I):
                continue
            items.append(LineItem(line_number=len(items) + 1, description=desc,
                                  quantity=qty, unit_price=rate, line_total=amt))
    return items


def apply_fallbacks(inv: ExtractedInvoice, text: str, rules: Rules) -> ExtractedInvoice:
    if not inv.invoice_number:
        if m := _INV_NO_LABELLED.search(text):  # "Invoice Number INV-3337" beats the generic pattern
            if any(ch.isdigit() for ch in m.group(1)):
                inv.invoice_number = m.group(1)
    if not inv.invoice_number:
        for m in _INV_NO.finditer(text):  # try every match: the first "Invoice" is often just the title
            cand = m.group(1)
            if any(ch.isdigit() for ch in cand):
                inv.invoice_number = cand
                break
    if not inv.po_number and (m := _PO_NO.search(text)):
        inv.po_number = m.group(1).upper().replace(" ", "-")
    if inv.total_amount is None:
        totals = [parse_amount(x) for x in _TOTAL.findall(text)]
        totals = [t for t in totals if t is not None]
        if totals:
            inv.total_amount = max(totals)
    if inv.subtotal is None and (m := _SUBTOTAL.search(text)):
        inv.subtotal = parse_amount(m.group(1))
    if inv.tax_amount is None and (m := _TAX.search(text)):
        inv.tax_amount = parse_amount(m.group(1))
    if inv.invoice_date is None:
        if m := _LABELLED_DATE.search(text):
            inv.invoice_date = parse_date(m.group(1)) or parse_date(_fix_ocr_month(m.group(1)))
        if inv.invoice_date is None:
            for cand in _DATE.findall(text):
                if (d := parse_date(cand)) is not None:
                    inv.invoice_date = d
                    break
    if not inv.vendor_name:
        for ln in text.splitlines()[:25]:
            ln = ln.strip()
            if 3 < len(ln) < 80 and _COMPANY.match(ln):
                inv.vendor_name = re.sub(r"\s+(?:order|invoice)\b.*$", "", ln, flags=re.I).strip()
                break
    if not inv.line_items:
        inv.line_items = _line_items(text)
    if not inv.currency:
        for sym, iso in rules.currency_symbol_map.items():
            if sym in text:
                inv.currency = iso
                break
        if not inv.currency and (m := _CCY.search(text)):
            inv.currency = m.group(1)
    return inv


def _ground(inv: ExtractedInvoice, text: str, rules: Rules) -> ExtractedInvoice:
    """Drop model guesses that are not present in the document (small models hallucinate on blank/partial files)."""
    up = text.upper()
    if inv.currency:
        symbols = [sym for sym, iso in rules.currency_symbol_map.items() if iso == inv.currency]
        if inv.currency not in up and not any(sym in text for sym in symbols):
            inv.currency = None
    if inv.invoice_date and str(inv.invoice_date.year) not in text and f"{inv.invoice_date.year % 100:02d}" not in text:
        inv.invoice_date = None
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
    if not data:
        inv.extras.setdefault("parse_mode", "regex_fallback")  # visible in the audit record
    source = translated_text + "\n" + (raw_text or "")
    inv = _ground(inv, source, rules)
    inv = apply_fallbacks(inv, source, rules)
    for li in inv.line_items:  # keep money as Decimal with sane precision
        for attr in ("quantity", "unit_price", "line_total"):
            v = getattr(li, attr)
            if isinstance(v, Decimal):
                setattr(li, attr, v)
    return inv
