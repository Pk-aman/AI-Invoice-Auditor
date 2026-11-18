-- ERP master data (mock). Read only through the ai-auditor-services HTTP API.
CREATE TABLE erp.vendors (
    vendor_id    VARCHAR(16)  PRIMARY KEY,
    vendor_name  VARCHAR(255) NOT NULL,
    country      VARCHAR(64),
    currency     CHAR(3)      NOT NULL
);
CREATE INDEX idx_vendors_name_trgm ON erp.vendors USING gin (vendor_name gin_trgm_ops);

CREATE TABLE erp.purchase_orders (
    po_number  VARCHAR(32) PRIMARY KEY,
    vendor_id  VARCHAR(16) NOT NULL REFERENCES erp.vendors(vendor_id)
);
CREATE INDEX idx_po_vendor ON erp.purchase_orders(vendor_id);

CREATE TABLE erp.po_line_items (
    id           BIGSERIAL PRIMARY KEY,
    po_number    VARCHAR(32)   NOT NULL REFERENCES erp.purchase_orders(po_number),
    line_number  INTEGER       NOT NULL,
    item_code    VARCHAR(32)   NOT NULL,
    description  TEXT,
    qty          NUMERIC(12,3) NOT NULL,
    unit_price   NUMERIC(14,4) NOT NULL,
    currency     CHAR(3)       NOT NULL,
    UNIQUE (po_number, line_number)
);
CREATE INDEX idx_po_lines_item ON erp.po_line_items(item_code);

CREATE TABLE erp.sku_master (
    item_code  VARCHAR(32) PRIMARY KEY,
    category   VARCHAR(64),
    uom        VARCHAR(16),
    gst_rate   NUMERIC(5,2)
);

GRANT SELECT ON ALL TABLES IN SCHEMA erp TO services_rw;
