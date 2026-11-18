import re
from difflib import SequenceMatcher

from sqlalchemy import Connection, text


def get_vendor(conn: Connection, vendor_id: str) -> dict | None:
    row = conn.execute(
        text("SELECT vendor_id, vendor_name, country, currency FROM erp.vendors WHERE vendor_id = :v"),
        {"v": vendor_id},
    ).mappings().first()
    return dict(row) if row else None


def get_sku(conn: Connection, code: str) -> dict | None:
    row = conn.execute(
        text("SELECT item_code, category, uom, gst_rate FROM erp.sku_master WHERE item_code = :c"),
        {"c": code},
    ).mappings().first()
    return dict(row) if row else None


def get_po(conn: Connection, po_number: str) -> dict | None:
    head = conn.execute(
        text("SELECT po_number, vendor_id FROM erp.purchase_orders WHERE po_number = :p"),
        {"p": po_number},
    ).mappings().first()
    if not head:
        return None
    lines = conn.execute(
        text(
            "SELECT line_number, item_code, description, qty, unit_price, currency "
            "FROM erp.po_line_items WHERE po_number = :p ORDER BY line_number"
        ),
        {"p": po_number},
    ).mappings().all()
    return {**head, "line_items": [dict(x) for x in lines]}


def list_pos_for_vendor(conn: Connection, vendor_id: str) -> list[dict]:
    nums = conn.execute(
        text("SELECT po_number FROM erp.purchase_orders WHERE vendor_id = :v ORDER BY po_number"),
        {"v": vendor_id},
    ).scalars().all()
    return [po for n in nums if (po := get_po(conn, n))]


# --- fuzzy vendor search -------------------------------------------------------------------------
# services_rw has no access to the public schema, so pg_trgm functions are not callable. The vendor
# master is small, so ranking is done here (difflib + token overlap) over the full table.
_STOP = {"ltd", "limited", "co", "company", "inc", "gmbh", "sa", "s", "a", "pvt", "llc", "corp", "the"}
# industry words shared by many vendors; they must not make unrelated vendors look alike
_GENERIC = {"logistics", "logistic", "transport", "transporte", "freight", "courier", "couriers", "services", "service", "shipping"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower().replace("í", "i").replace("é", "e")).strip()


def _distinctive(s: str) -> list[str]:
    toks = [t for t in _norm(s).split() if t not in _STOP]
    core = [t for t in toks if t not in _GENERIC]
    return core or toks


def similarity(a: str, b: str) -> float:
    ta, tb = _distinctive(a), _distinctive(b)
    if not ta or not tb:
        return 0.0
    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    overlap = len(set(ta) & set(tb)) / min(len(set(ta)), len(set(tb)))
    return round(max(ratio, 0.5 * ratio + 0.5 * overlap), 4)


def search_vendors(conn: Connection, name: str, limit: int = 5, min_score: float = 0.35) -> list[dict]:
    rows = conn.execute(
        text("SELECT vendor_id, vendor_name, country, currency FROM erp.vendors")
    ).mappings().all()
    scored = [{**dict(r), "score": similarity(name, r["vendor_name"])} for r in rows]
    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:limit]
