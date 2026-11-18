from pydantic import BaseModel


class Vendor(BaseModel):
    vendor_id: str
    vendor_name: str
    country: str | None = None
    currency: str


class VendorMatch(Vendor):
    score: float


class PoLine(BaseModel):
    line_number: int
    item_code: str
    description: str | None = None
    qty: float
    unit_price: float
    currency: str


class PurchaseOrder(BaseModel):
    po_number: str
    vendor_id: str
    line_items: list[PoLine]


class Sku(BaseModel):
    item_code: str
    category: str | None = None
    uom: str | None = None
    gst_rate: float | None = None
