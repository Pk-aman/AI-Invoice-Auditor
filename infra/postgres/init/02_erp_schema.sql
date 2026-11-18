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

-- ERP mock data (vendors, SKUs, purchase orders)

INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-001', 'Global Logistics Ltd', 'UK', 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-002', 'BlueOcean Transport Co.', 'USA', 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-003', 'Transporte Ibérico S.A.', 'Spain', 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-004', 'HafenLogistik GmbH', 'Germany', 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-005', 'SwiftMove Couriers', 'USA', 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.vendors (vendor_id, vendor_name, country, currency) VALUES ('VEND-006', 'OceanFreight Pvt Ltd', 'India', 'INR') ON CONFLICT DO NOTHING;

INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-001', 'Packaging', 'roll', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-002', 'Safety', 'pair', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-003', 'Safety', 'piece', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-101', 'Logistics', 'piece', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-201', 'Packaging', 'box', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-202', 'Packaging', 'label', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-301', 'Transport', 'crate', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-302', 'Safety', 'vest', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-401', 'Charges', 'service', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-402', 'Packaging', 'box', 10) ON CONFLICT DO NOTHING;
INSERT INTO erp.sku_master (item_code, category, uom, gst_rate) VALUES ('SKU-501', 'Transport', 'service', 10) ON CONFLICT DO NOTHING;

INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1001', 'VEND-001') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1001', 1, 'SKU-001', 'Pallet Wrapping Film', 50, 12.0, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1001', 2, 'SKU-002', 'Industrial Gloves', 120, 3.0, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1001', 3, 'SKU-003', 'Safety Helmets', 30, 15.0, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1002', 'VEND-002') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1002', 1, 'SKU-101', 'Container Seals', 200, 1.25, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1003', 'VEND-003') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1003', 1, 'SKU-201', 'Cajas de carton', 100, 2.5, 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1003', 2, 'SKU-202', 'Etiquetas adhesivas', 300, 0.3, 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1004', 'VEND-004') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1004', 1, 'SKU-301', 'Transportkisten', 40, 25.0, 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1004', 2, 'SKU-302', 'Sicherheitswesten', 60, 10.0, 'EUR') ON CONFLICT DO NOTHING;
INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1005', 'VEND-005') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1005', 1, 'SKU-401', 'Fuel Surcharge', 1, 120.0, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1005', 2, 'SKU-402', 'Express Delivery Boxes', 25, 4.5, 'USD') ON CONFLICT DO NOTHING;
INSERT INTO erp.purchase_orders (po_number, vendor_id) VALUES ('PO-1006', 'VEND-006') ON CONFLICT DO NOTHING;
INSERT INTO erp.po_line_items (po_number, line_number, item_code, description, qty, unit_price, currency) VALUES ('PO-1006', 1, 'SKU-501', 'Container Transport', 5, 8000.0, 'INR') ON CONFLICT DO NOTHING;
