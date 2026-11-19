-- Audit trail: the system of record. Adapted from the project brief's suggested schema.
--
-- Persistence lifecycle (two business-data writes per invoice):
--   SQL Write #1  agent: ingest_translation()  -> INSERT one invoice_audit row (text + checksums only)
--   SQL Write #2  agent: finalize_invoice()    -> UPDATE the row + INSERT line items, discrepancies, report(s),
--                                                 not-yet-inserted human feedback, in ONE transaction
-- Everything in between lives in the LangGraph checkpoint, Qdrant and Redis.
-- processing_events, human_feedback and review_decisions are append-only audit LOGS (operational history).
--
-- Rules: money = NUMERIC; dates = DATE/TIMESTAMPTZ; line items are rows; raw text retained;
--        human corrections are INSERT-only; ai_audit_report is immutable once written.
-- Existing installations: see infra/postgres/migrations/ (applied by infra/postgres/migrate.py).

CREATE TABLE audit.invoice_audit (
    invoice_id              UUID PRIMARY KEY,
    -- ---- SQL Write #1 (post-translation ingestion) ----
    file_path               VARCHAR(512),                 -- reference, not the file
    file_checksum           VARCHAR(64) UNIQUE NOT NULL,  -- sha256 of the raw file: detects re-upload
    content_checksum        VARCHAR(64) UNIQUE NOT NULL,  -- sha256 of normalised translated text: detects equivalent copies
    extraction_method       VARCHAR(32),                  -- pymupdf | tesseract | pymupdf+tesseract | docx
    raw_extracted_text      TEXT,                         -- full text, pre-translation
    translated_text         TEXT,
    source_language         VARCHAR(8),
    translation_confidence  NUMERIC(4,3),
    ocr_confidence          NUMERIC(4,3),
    thread_id               VARCHAR(128),                 -- links to LangGraph state
    status                  VARCHAR(32) NOT NULL DEFAULT 'INGESTED'
                            CHECK (status IN ('INGESTED','APPROVED','REJECTED','SKIPPED_MAX_RETRIES','LEGACY_FINALIZED')),
    human_check_count       INTEGER NOT NULL DEFAULT 0 CHECK (human_check_count BETWEEN 0 AND 4),
    processed_at            TIMESTAMPTZ DEFAULT now(),
    -- ---- SQL Write #2 (finalization) ----
    invoice_number          VARCHAR(64),
    vendor_name             VARCHAR(255),
    vendor_id               VARCHAR(16),                  -- resolved ERP id (nullable)
    po_number               VARCHAR(64),
    invoice_date            DATE,
    currency                CHAR(3),
    total_amount            NUMERIC(14,2),
    validation_status       VARCHAR(32) CHECK (validation_status IN ('passed','failed','partial','skipped_max_retries')),
    recommendation          VARCHAR(32) CHECK (recommendation   IN ('approve','review','reject')),
    validation_run          INTEGER NOT NULL DEFAULT 1,
    extracted_raw           JSONB,                        -- variable leftovers
    report_path             VARCHAR(512),
    ai_audit_report         TEXT,                         -- immutable snapshot of the first AI report (canonical JSON)
    finalized_at            TIMESTAMPTZ,
    CONSTRAINT chk_finalized CHECK ((status = 'INGESTED') = (finalized_at IS NULL))
);
CREATE INDEX idx_audit_vendor   ON audit.invoice_audit(vendor_name);
CREATE INDEX idx_audit_status   ON audit.invoice_audit(validation_status, recommendation);
CREATE INDEX idx_audit_date     ON audit.invoice_audit(processed_at);

-- Immutability guards: evidence columns can be set once, never rewritten.
CREATE FUNCTION audit.guard_invoice_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.ai_audit_report IS NOT NULL AND NEW.ai_audit_report IS DISTINCT FROM OLD.ai_audit_report THEN
        RAISE EXCEPTION 'audit.invoice_audit.ai_audit_report is immutable';
    END IF;
    IF NEW.file_checksum IS DISTINCT FROM OLD.file_checksum
       OR NEW.content_checksum IS DISTINCT FROM OLD.content_checksum
       OR NEW.raw_extracted_text IS DISTINCT FROM OLD.raw_extracted_text
       OR NEW.translated_text IS DISTINCT FROM OLD.translated_text THEN
        RAISE EXCEPTION 'audit.invoice_audit ingestion evidence is immutable';
    END IF;
    IF OLD.finalized_at IS NOT NULL THEN
        RAISE EXCEPTION 'audit.invoice_audit row % is finalized', OLD.invoice_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_guard_invoice_audit BEFORE UPDATE ON audit.invoice_audit
    FOR EACH ROW EXECUTE FUNCTION audit.guard_invoice_audit();

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
    superseded     BOOLEAN NOT NULL DEFAULT FALSE,        -- earlier validation runs are kept, flagged superseded
    field_name     VARCHAR(64),
    invoice_value  TEXT,
    erp_value      TEXT,
    deviation_pct  NUMERIC(8,4),
    severity       VARCHAR(16),                           -- low | medium | high | critical
    detected_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_disc_invoice ON audit.discrepancies(invoice_id, validation_run);

-- One generated report per validation run (run 1 == the immutable invoice_audit.ai_audit_report).
CREATE TABLE audit.validation_reports (
    invoice_id      UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    validation_run  INTEGER NOT NULL,
    report          JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (invoice_id, validation_run)
);

CREATE TABLE audit.human_feedback (
    id               BIGSERIAL PRIMARY KEY,
    invoice_id       UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    field_name       VARCHAR(64) NOT NULL,
    original_value   TEXT,
    corrected_value  TEXT,
    corrected_by     VARCHAR(128),
    corrected_at     TIMESTAMPTZ DEFAULT now(),
    revalidated      BOOLEAN DEFAULT FALSE,
    review_round     INTEGER NOT NULL DEFAULT 0            -- 0 = standalone API correction; >=1 = review round
);
CREATE INDEX idx_feedback_invoice ON audit.human_feedback(invoice_id);
-- retries of the same review round must not duplicate rows
CREATE UNIQUE INDEX uq_feedback_round_field ON audit.human_feedback(invoice_id, review_round, field_name) WHERE review_round > 0;

-- Reviewer decisions (approve / reject / reject_and_revalidate) with their notes.
CREATE TABLE audit.review_decisions (
    id                 BIGSERIAL PRIMARY KEY,
    invoice_id         UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    review_round       INTEGER NOT NULL,
    decision           VARCHAR(32) NOT NULL CHECK (decision IN ('approve','reject','reject_and_revalidate')),
    reviewer           VARCHAR(128) NOT NULL,
    comments           TEXT,
    human_check_count  INTEGER NOT NULL,
    decided_at         TIMESTAMPTZ DEFAULT now(),
    UNIQUE (invoice_id, review_round)
);

-- Every detected/uploaded file, including duplicates and failures (invoice_id may be NULL early on)
CREATE TABLE audit.processing_events (
    id             BIGSERIAL PRIMARY KEY,
    invoice_id     UUID,
    file_path      VARCHAR(512),
    file_checksum  VARCHAR(64),
    stage          VARCHAR(32) NOT NULL,   -- QUEUED|DETECTED|EXTRACT|TRANSLATE|DEDUPE|INGEST|PARSE|DATA_VALIDATION|BUSINESS_VALIDATION|REPORT|INDEX|HUMAN_REVIEW|REVALIDATE|FINALIZE|COMPLETED
    state          VARCHAR(16) NOT NULL,   -- STARTED|OK|FAILED|DUPLICATE|REJECTED|PENDING
    detail         JSONB,
    created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_events_checksum ON audit.processing_events(file_checksum, created_at);
CREATE INDEX idx_events_invoice  ON audit.processing_events(invoice_id, created_at);

-- RAG quality scores (RAGAS): answer_relevance, faithfulness (groundedness), context_relevance
CREATE TABLE audit.rag_evaluations (
    id                 UUID PRIMARY KEY,
    invoice_id         UUID,
    question           TEXT,
    answer             TEXT,
    route              VARCHAR(16),
    state              VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','done','failed')),
    error              TEXT,
    context_relevance  NUMERIC(4,3),
    faithfulness       NUMERIC(4,3),
    answer_relevance   NUMERIC(4,3),
    low_confidence     BOOLEAN,
    created_at         TIMESTAMPTZ DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

-- Applied-migration ledger (see infra/postgres/migrate.py). A fresh install is born at version 1.
CREATE TABLE audit.schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO audit.schema_migrations (version, name) VALUES (1, 'two_write_lifecycle');

-- ---- Privileges --------------------------------------------------------
-- agent: Write #1 INSERTs the row; Write #2 UPDATEs ONLY the finalization columns (never evidence/checksums).
-- No DELETE anywhere. Line items/discrepancies/reports/feedback/decisions/events are INSERT-only.
GRANT SELECT, INSERT ON audit.invoice_audit, audit.invoice_line_items, audit.discrepancies,
      audit.validation_reports, audit.human_feedback, audit.review_decisions, audit.processing_events TO agent_rw;
GRANT UPDATE (invoice_number, vendor_name, vendor_id, po_number, invoice_date, currency, total_amount,
              validation_status, recommendation, validation_run, extracted_raw, report_path, ai_audit_report,
              status, human_check_count, finalized_at) ON audit.invoice_audit TO agent_rw;
GRANT UPDATE (revalidated) ON audit.human_feedback TO agent_rw;   -- flag only; values stay immutable
GRANT SELECT, INSERT, UPDATE ON audit.rag_evaluations TO agent_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO agent_rw;

-- services: read everything for the UI; INSERT-only standalone corrections and QUEUED events.
GRANT SELECT ON ALL TABLES IN SCHEMA audit TO services_rw;
GRANT INSERT ON audit.human_feedback, audit.processing_events TO services_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO services_rw;
REVOKE SELECT ON audit.schema_migrations FROM services_rw;
