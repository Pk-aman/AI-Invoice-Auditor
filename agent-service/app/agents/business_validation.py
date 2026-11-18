"""Business Validation agent: compare the invoice with ERP master data, obtained ONLY via the ERP HTTP API."""
from __future__ import annotations

import difflib
import logging
from decimal import Decimal

from app.clients.erp_client import ErpClient
from app.core.rules import Rules
from app.schemas.models import (
    BusinessResult,
    Discrepancy,
    ErpPoLine,
    ErpPurchaseOrder,
    ErpVendor,
    ExtractedInvoice,
    Severity,
)

log = logging.getLogger(__name__)

_DESC_MATCH_MIN = 0.8


def deviation_pct(invoice_value: Decimal, erp_value: Decimal) -> Decimal:
    if erp_value == 0:
        return Decimal("0") if invoice_value == 0 else Decimal("100")
    return (abs(invoice_value - erp_value) / abs(erp_value) * 100).quantize(Decimal("0.0001"))


def severity_for(dev: Decimal, tol: float) -> Severity:
    """Only called for deviations beyond tolerance."""
    if tol > 0:
        if dev <= Decimal(str(tol)) * 2:
            return "low"
        if dev <= Decimal(str(tol)) * 4:
            return "medium"
        return "high"
    return "medium" if dev <= 5 else "high"  # zero-tolerance fields (quantity)


def _resolve_vendor(inv: ExtractedInvoice, erp: ErpClient, min_score: float) -> tuple[ErpVendor | None, str | None]:
    if inv.vendor_id:
        v = erp.get_vendor(inv.vendor_id)
        if v:
            return v, None
    if inv.vendor_name:
        matches = erp.search_vendors(inv.vendor_name, limit=3)
        if matches and (matches[0].score or 0) >= min_score:
            return matches[0], f"vendor matched by name ({matches[0].score:.2f})"
    return None, None


def _match_line(item_code: str | None, description: str | None, po_lines: list[ErpPoLine], used: set[int]) -> ErpPoLine | None:
    if item_code:
        for pl in po_lines:
            if pl.line_number not in used and pl.item_code.lower() == item_code.lower():
                return pl
    if description:
        best, best_ratio = None, 0.0
        for pl in po_lines:
            if pl.line_number in used or not pl.description:
                continue
            r = difflib.SequenceMatcher(None, description.lower(), pl.description.lower()).ratio()
            if r > best_ratio:
                best, best_ratio = pl, r
        if best and best_ratio >= _DESC_MATCH_MIN:
            return best
    return None


def _infer_po(inv: ExtractedInvoice, pos: list[ErpPurchaseOrder]) -> ErpPurchaseOrder | None:
    codes = {li.item_code.lower() for li in inv.line_items if li.item_code}
    best, best_overlap = None, 0
    for po in pos:
        overlap = len(codes & {pl.item_code.lower() for pl in po.line_items})
        if overlap > best_overlap:
            best, best_overlap = po, overlap
    return best


def business_validate(inv: ExtractedInvoice, rules: Rules, erp: ErpClient, *, vendor_min_score: float = 0.5) -> BusinessResult:
    res = BusinessResult()
    d = res.discrepancies

    # 1. vendor
    vendor, note = _resolve_vendor(inv, erp, vendor_min_score)
    if vendor:
        res.vendor_found, res.vendor_id = True, vendor.vendor_id
        if note:
            res.notes.append(note)
    else:
        d.append(Discrepancy(field_name="vendor", invoice_value=inv.vendor_id or inv.vendor_name, erp_value=None,
                             severity="critical", code="vendor_not_found"))

    # 2. purchase order
    po: ErpPurchaseOrder | None = None
    if inv.po_number:
        po = erp.get_po(inv.po_number)
        if po is None:
            d.append(Discrepancy(field_name="po_number", invoice_value=inv.po_number, erp_value=None, severity="high", code="po_not_found"))
    elif vendor:
        po = _infer_po(inv, erp.list_pos(vendor.vendor_id))
        if po:
            res.po_inferred = True
            res.notes.append(f"PO {po.po_number} inferred from vendor and item codes")
        else:
            d.append(Discrepancy(field_name="po_number", invoice_value=None, erp_value=None, severity="high", code="po_not_found"))
    else:
        d.append(Discrepancy(field_name="po_number", invoice_value=None, erp_value=None, severity="high", code="po_not_found"))
    if po:
        res.po_number = po.po_number
        if vendor and po.vendor_id != vendor.vendor_id:
            d.append(Discrepancy(field_name="vendor", invoice_value=vendor.vendor_id, erp_value=po.vendor_id, severity="high", code="vendor_po_mismatch"))

    # 3. currency (header) vs PO line currencies
    if po and inv.currency:
        po_ccy = {pl.currency for pl in po.line_items}
        if po_ccy and inv.currency not in po_ccy:
            d.append(Discrepancy(field_name="currency", invoice_value=inv.currency, erp_value=",".join(sorted(po_ccy)),
                                 severity="high", code="currency_mismatch"))

    # 4. lines: quantity / unit price vs PO; SKU existence
    used: set[int] = set()
    skus = {}
    for n, li in enumerate(inv.line_items, start=1):
        if li.item_code and li.item_code not in skus:
            skus[li.item_code] = erp.get_sku(li.item_code)
        if li.item_code and skus.get(li.item_code) is None:
            d.append(Discrepancy(field_name=f"line[{n}].item_code", invoice_value=li.item_code, erp_value=None,
                                 severity="medium", code="sku_not_found"))
        if not po:
            continue
        pl = _match_line(li.item_code, li.description, po.line_items, used)
        if pl is None:
            d.append(Discrepancy(field_name=f"line[{n}].item_code", invoice_value=li.item_code or li.description, erp_value=None,
                                 severity="high", code="line_not_on_po"))
            continue
        used.add(pl.line_number)
        if li.quantity is not None:
            dev = deviation_pct(li.quantity, pl.qty)
            if dev > Decimal(str(rules.qty_tol_pct)):
                d.append(Discrepancy(field_name=f"line[{n}].quantity", invoice_value=str(li.quantity), erp_value=str(pl.qty),
                                     deviation_pct=dev, severity=severity_for(dev, rules.qty_tol_pct), code="quantity_mismatch"))
        if li.unit_price is not None:
            dev = deviation_pct(li.unit_price, pl.unit_price)
            if dev > Decimal(str(rules.price_tol_pct)):
                d.append(Discrepancy(field_name=f"line[{n}].unit_price", invoice_value=str(li.unit_price), erp_value=str(pl.unit_price),
                                     deviation_pct=dev, severity=severity_for(dev, rules.price_tol_pct), code="price_mismatch"))

    # 5. tax amount vs SKU GST rates
    if inv.tax_amount is not None:
        expected = Decimal(0)
        covered = False
        for li in inv.line_items:
            sku = skus.get(li.item_code) if li.item_code else None
            base = li.line_total if li.line_total is not None else (
                li.quantity * li.unit_price if li.quantity is not None and li.unit_price is not None else None)
            if sku and sku.gst_rate is not None and base is not None:
                expected += base * sku.gst_rate / Decimal(100)
                covered = True
        if covered:
            dev = deviation_pct(inv.tax_amount, expected)
            if dev > Decimal(str(rules.tax_tol_pct)):
                d.append(Discrepancy(field_name="tax_amount", invoice_value=str(inv.tax_amount), erp_value=f"{expected:.2f}",
                                     deviation_pct=dev, severity=severity_for(dev, rules.tax_tol_pct), code="tax_mismatch"))
    return res
