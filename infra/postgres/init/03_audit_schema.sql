-- Audit trail: the system of record. Adapted from the project brief's suggested schema.
-- Rules: money = NUMERIC; dates = DATE/TIMESTAMPTZ; line items are rows; raw text retained;
--        human corrections are INSERT-only (no UPDATE/DELETE grant on human_feedback).

CREATE TABLE audit.invoice_audit (
    invoice_id              UUID PRIMARY KEY,
    file_path               VARCHAR(512),                 -- reference, not the file
    file_checksum           VARCHAR(64) UNIQUE NOT NULL,  -- detects reprocessing
    extraction_method       VARCHAR(32),                  -- pdfplumber | docx | tesseract
    raw_extracted_text      TEXT,                         -- full text, pre-translation
    translated_text         TEXT,
    source_language         VARCHAR(8),
    translation_confidence  NUMERIC(4,3),
    ocr_confidence          NUMERIC(4,3),
    invoice_number          VARCHAR(64),
    vendor_name             VARCHAR(255),
    vendor_id               VARCHAR(16),                  -- resolved ERP id (nullable)
    po_number               VARCHAR(64),
    invoice_date            DATE,
    currency                CHAR(3),
    total_amount            NUMERIC(14,2),
    validation_status       VARCHAR(32) CHECK (validation_status IN ('passed','failed','partial')),
    recommendation          VARCHAR(32) CHECK (recommendation   IN ('approve','review','reject')),
    validation_run          INTEGER NOT NULL DEFAULT 1,
    extracted_raw           JSONB,                        -- variable leftovers
    report_path             VARCHAR(512),
    thread_id               VARCHAR(128),                 -- links to LangGraph state
    processed_at            TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_audit_vendor   ON audit.invoice_audit(vendor_name);
CREATE INDEX idx_audit_status   ON audit.invoice_audit(validation_status, recommendation);
CREATE INDEX idx_audit_date     ON audit.invoice_audit(processed_at);

CREATE TABLE audit.invoice_line_items (
    id           BIGSERIAL PRIMARY KEY,
    invoice_id   UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    line_number  INTEGER,
    item_code    VARCHAR(64),
    description  TEXT,
    quantity     NUMERIC(12,3),
    unit_price   NUMERIC(14,4),
    line_total   NUMERIC(14,2)
);
CREATE INDEX idx_lines_invoice ON audit.invoice_line_items(invoice_id);

CREATE TABLE audit.discrepancies (
    id             BIGSERIAL PRIMARY KEY,
    invoice_id     UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    validation_run INTEGER NOT NULL DEFAULT 1,
    superseded     BOOLEAN NOT NULL DEFAULT FALSE,        -- older runs are kept, never deleted
    field_name     VARCHAR(64),
    invoice_value  TEXT,
    erp_value      TEXT,
    deviation_pct  NUMERIC(8,4),
    severity       VARCHAR(16),                           -- low | medium | high | critical
    detected_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_disc_invoice ON audit.discrepancies(invoice_id, validation_run);

CREATE TABLE audit.human_feedback (
    id               BIGSERIAL PRIMARY KEY,
    invoice_id       UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    field_name       VARCHAR(64) NOT NULL,
    original_value   TEXT,
    corrected_value  TEXT,
    corrected_by     VARCHAR(128),
    corrected_at     TIMESTAMPTZ DEFAULT now(),
    revalidated      BOOLEAN DEFAULT FALSE
);
CREATE INDEX idx_feedback_invoice ON audit.human_feedback(invoice_id);

-- Every detected/uploaded file, including duplicates and failures (invoice_id may be NULL early on)
CREATE TABLE audit.processing_events (
    id             BIGSERIAL PRIMARY KEY,
    invoice_id     UUID,
    file_path      VARCHAR(512),
    file_checksum  VARCHAR(64),
    stage          VARCHAR(32) NOT NULL,   -- QUEUED|DETECTED|EXTRACT|TRANSLATE|PARSE|DATA_VALIDATION|BUSINESS_VALIDATION|REPORT|INDEX|COMPLETED
    state          VARCHAR(16) NOT NULL,   -- STARTED|OK|FAILED|DUPLICATE|REJECTED
    detail         JSONB,
    created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_events_checksum ON audit.processing_events(file_checksum, created_at);
CREATE INDEX idx_events_invoice  ON audit.processing_events(invoice_id, created_at);

CREATE TABLE audit.rag_evaluations (
    id                 UUID PRIMARY KEY,
    question           TEXT,
    answer             TEXT,
    route              VARCHAR(16),
    relevance          NUMERIC(4,3),
    groundedness       NUMERIC(4,3),
    context_relevance  NUMERIC(4,3),
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- ---- Privileges --------------------------------------------------------
-- agent: writes the pipeline output. Cannot touch human_feedback beyond reading it. No DELETE anywhere.
GRANT SELECT, INSERT, UPDATE ON audit.invoice_audit, audit.invoice_line_items, audit.discrepancies,
      audit.processing_events, audit.rag_evaluations TO agent_rw;
GRANT SELECT ON audit.human_feedback TO agent_rw;
GRANT UPDATE (revalidated) ON audit.human_feedback TO agent_rw;   -- flag only; values stay immutable
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO agent_rw;

-- services: read everything for the UI; INSERT-only corrections and QUEUED events.
GRANT SELECT ON ALL TABLES IN SCHEMA audit TO services_rw;
GRANT INSERT ON audit.human_feedback, audit.processing_events TO services_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO services_rw;
