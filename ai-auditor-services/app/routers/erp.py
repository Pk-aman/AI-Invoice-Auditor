from fastapi import APIRouter, Depends, Query
from sqlalchemy import Connection

from app.core.db import get_conn
from app.core.errors import AppError
from app.repositories import erp_repo
from app.schemas.erp import PurchaseOrder, Sku, Vendor, VendorMatch

router = APIRouter(tags=["erp"])


@router.get("/po/{po_number}", response_model=PurchaseOrder)
def get_po(po_number: str, conn: Connection = Depends(get_conn)):
    po = erp_repo.get_po(conn, po_number)
    if not po:
        raise AppError(404, "po_not_found", f"Purchase order {po_number} not found.")
    return po


@router.get("/vendor/{vendor_id}", response_model=Vendor)
def get_vendor(vendor_id: str, conn: Connection = Depends(get_conn)):
    v = erp_repo.get_vendor(conn, vendor_id)
    if not v:
        raise AppError(404, "vendor_not_found", f"Vendor {vendor_id} not found.")
    return v


@router.get("/sku/{sku_code}", response_model=Sku)
def get_sku(sku_code: str, conn: Connection = Depends(get_conn)):
    s = erp_repo.get_sku(conn, sku_code)
    if not s:
        raise AppError(404, "sku_not_found", f"SKU {sku_code} not found.")
    return s


@router.get("/api/v1/erp/vendors/search", response_model=list[VendorMatch])
def search_vendors(name: str = Query(min_length=1, max_length=255), limit: int = Query(5, ge=1, le=20),
                   conn: Connection = Depends(get_conn)):
    return erp_repo.search_vendors(conn, name, limit)


@router.get("/api/v1/erp/pos", response_model=list[PurchaseOrder])
def list_pos(vendor_id: str, conn: Connection = Depends(get_conn)):
    return erp_repo.list_pos_for_vendor(conn, vendor_id)
