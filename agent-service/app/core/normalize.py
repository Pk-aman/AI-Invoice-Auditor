"""Pure normalisation helpers: amounts, dates, currency, text."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y",
    "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d-%B-%Y", "%m/%d/%Y", "%d/%m/%y", "%d.%m.%y",
)


def parse_amount(value: object) -> Decimal | None:
    """'1,234.56' / '1.234,56' / '134,462,512.00' / 12 / '$ 5 000' -> Decimal; None if not numeric."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = re.sub(r"[^\d,.\-]", "", str(value).strip())
    if not s or s in {"-", ".", ","}:
        return None
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        # the right-most separator is the decimal mark
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):  # 12,50 -> decimal comma
            s = parts[0] + "." + parts[1]
        else:  # 1,234 / 1,234,567 -> thousands
            s = s.replace(",", "")
    elif s.count(".") > 1:  # 1.234.567 -> thousands
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    for fmt in _DATE_FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        # OCR noise can yield absurd years (e.g. 2224); treat those as unreadable, not as data.
        if 1990 <= d.year <= date.today().year + 1:
            return d
        return None
    return None


def normalize_currency(value: object, symbol_map: dict[str, str]) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s in symbol_map:
        return symbol_map[s]
    up = s.upper()
    if re.fullmatch(r"[A-Z]{3}", up):
        return up
    for sym, iso in symbol_map.items():
        if sym in s:
            return iso
    return None


def clean_text(value: str) -> str:
    """PostgreSQL TEXT cannot hold NUL bytes."""
    return value.replace("\x00", "")


def dec_str(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f") if value == value.to_integral() else str(value)


def jsonable(obj):
    """Recursively convert Decimal/date to JSON-safe values (for checkpoint state / reports)."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj


def normalize_content(translated_text: str) -> str:
    """Canonical form used for content de-duplication (must stay identical to infra/postgres/migrate.py)."""
    return " ".join(translated_text.lower().split())


def content_checksum(translated_text: str) -> str:
    """SHA-256 of the normalised (lower-cased, whitespace-collapsed) English text."""
    return hashlib.sha256(normalize_content(translated_text).encode("utf-8")).hexdigest()
