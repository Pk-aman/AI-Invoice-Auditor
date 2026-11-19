-- Migration 001 (step 1/2): two-write lifecycle, content checksum, immutable AI report, HITL, RAGAS columns.
-- Idempotent and additive: nothing is dropped or rewritten except renamed RAG evaluation columns.
-- content_checksum is added NULLable here and backfilled in Python (exact normalisation) before step 2
-- turns it NOT NULL + UNIQUE.

ALTER TABLE audit.invoice_audit
    ADD COLUMN IF NOT EXISTS content_checksum  VARCHAR(64),
    ADD COLUMN IF NOT EXISTS status            VARCHAR(32) NOT NULL DEFAULT 'INGESTED',
    ADD COLUMN IF NOT EXISTS human_check_count INTEGER     NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_audit_report   TEXT,
    ADD COLUMN IF NOT EXISTS finalized_at      TIMESTAMPTZ;

ALTER TABLE audit.human_feedback ADD COLUMN IF NOT EXISTS review_round INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS audit.validation_reports (
    invoice_id      UUID NOT NULL REFERENCES audit.invoice_audit(invoice_id),
    validation_run  INTEGER NOT NULL,
    report          JSONB NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (invoice_id, validation_run)
);

CREATE TABLE IF NOT EXISTS audit.review_decisions (
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

-- RAG evaluations: clear RAGAS names + state tracked in PostgreSQL (not Redis)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='audit' AND table_name='rag_evaluations' AND column_name='relevance') THEN
        ALTER TABLE audit.rag_evaluations RENAME COLUMN relevance TO answer_relevance;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='audit' AND table_name='rag_evaluations' AND column_name='groundedness') THEN
        ALTER TABLE audit.rag_evaluations RENAME COLUMN groundedness TO faithfulness;
    END IF;
END $$;
ALTER TABLE audit.rag_evaluations
    ADD COLUMN IF NOT EXISTS invoice_id     UUID,
    ADD COLUMN IF NOT EXISTS state          VARCHAR(16) NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS error          TEXT,
    ADD COLUMN IF NOT EXISTS low_confidence BOOLEAN,
    ADD COLUMN IF NOT EXISTS completed_at   TIMESTAMPTZ;
-- evaluations that already have scores are done
UPDATE audit.rag_evaluations SET state = 'done' WHERE answer_relevance IS NOT NULL AND state = 'pending';

-- Pre-migration invoices were finalized by the old single-pass pipeline: keep them readable, mark them legacy.
UPDATE audit.invoice_audit
   SET status = 'LEGACY_FINALIZED', finalized_at = COALESCE(processed_at, now())
 WHERE validation_status IS NOT NULL AND status = 'INGESTED';

-- the old CHECK only allowed passed|failed|partial
ALTER TABLE audit.invoice_audit DROP CONSTRAINT IF EXISTS invoice_audit_validation_status_check;
ALTER TABLE audit.invoice_audit ADD CONSTRAINT invoice_audit_validation_status_check
    CHECK (validation_status IN ('passed','failed','partial','skipped_max_retries'));
