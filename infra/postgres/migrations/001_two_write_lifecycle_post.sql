-- Migration 001 (step 2/2): constraints, immutability trigger, privileges (run after the content_checksum backfill).

ALTER TABLE audit.invoice_audit ALTER COLUMN content_checksum SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_audit_content_checksum ON audit.invoice_audit(content_checksum);

ALTER TABLE audit.invoice_audit DROP CONSTRAINT IF EXISTS invoice_audit_status_check;
ALTER TABLE audit.invoice_audit ADD CONSTRAINT invoice_audit_status_check
    CHECK (status IN ('INGESTED','APPROVED','REJECTED','SKIPPED_MAX_RETRIES','LEGACY_FINALIZED'));
ALTER TABLE audit.invoice_audit DROP CONSTRAINT IF EXISTS invoice_audit_human_check_count_check;
ALTER TABLE audit.invoice_audit ADD CONSTRAINT invoice_audit_human_check_count_check
    CHECK (human_check_count BETWEEN 0 AND 4);
ALTER TABLE audit.invoice_audit DROP CONSTRAINT IF EXISTS chk_finalized;
ALTER TABLE audit.invoice_audit ADD CONSTRAINT chk_finalized CHECK ((status = 'INGESTED') = (finalized_at IS NULL));

CREATE UNIQUE INDEX IF NOT EXISTS uq_feedback_round_field ON audit.human_feedback(invoice_id, review_round, field_name) WHERE review_round > 0;

ALTER TABLE audit.rag_evaluations DROP CONSTRAINT IF EXISTS rag_evaluations_state_check;
ALTER TABLE audit.rag_evaluations ADD CONSTRAINT rag_evaluations_state_check CHECK (state IN ('pending','done','failed'));

CREATE OR REPLACE FUNCTION audit.guard_invoice_audit() RETURNS trigger LANGUAGE plpgsql AS $$
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
DROP TRIGGER IF EXISTS trg_guard_invoice_audit ON audit.invoice_audit;
CREATE TRIGGER trg_guard_invoice_audit BEFORE UPDATE ON audit.invoice_audit
    FOR EACH ROW EXECUTE FUNCTION audit.guard_invoice_audit();

-- Privileges: agent loses blanket UPDATE/INSERT-everything and gets the narrower lifecycle grants.
REVOKE UPDATE ON audit.invoice_audit, audit.discrepancies, audit.invoice_line_items, audit.processing_events FROM agent_rw;
GRANT SELECT, INSERT ON audit.invoice_audit, audit.invoice_line_items, audit.discrepancies,
      audit.validation_reports, audit.human_feedback, audit.review_decisions, audit.processing_events TO agent_rw;
GRANT UPDATE (invoice_number, vendor_name, vendor_id, po_number, invoice_date, currency, total_amount,
              validation_status, recommendation, validation_run, extracted_raw, report_path, ai_audit_report,
              status, human_check_count, finalized_at) ON audit.invoice_audit TO agent_rw;
GRANT UPDATE (revalidated) ON audit.human_feedback TO agent_rw;
GRANT SELECT, INSERT, UPDATE ON audit.rag_evaluations TO agent_rw;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA audit TO agent_rw, services_rw;
GRANT SELECT ON audit.validation_reports, audit.review_decisions, audit.rag_evaluations TO services_rw;
