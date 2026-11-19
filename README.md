# AI Invoice Auditor

A containerized, multilingual invoice-validation system. Invoices (PDF / DOCX / scanned image) are ingested from a watched folder, extracted (with OCR), translated to English, validated against a mock ERP, reported, indexed for RAG, and reviewed by a human auditor in a Streamlit app.

Design docs: [Plan.md](Plan.md) (high level) · [agent-service/Plan.md](agent-service/Plan.md) · [ai-auditor-services/Plan.md](ai-auditor-services/Plan.md) · [ai-auditor-app/Plan.md](ai-auditor-app/Plan.md)

## Services

| Compose service | Directory | Host port → container | Role |
|---|---|---|---|
| `ai-auditor-app` (alias `streamlit-ui`) | `ai-auditor-app/` | 8501 → 8501 | Streamlit UI: Dashboard · Upload Invoice · Invoice Assistant |
| `ai-auditor-services` (alias `mock-erp`) | `ai-auditor-services/` | 8001 → 8000 | FastAPI: mock ERP + REST API for invoices, uploads, feedback, stats |
| `agent-service` (alias `agent`) | `agent-service/` | 8000 → 8000 | LangGraph pipeline (with a human-review pause), OCR, translation, validation, reporting, RAG |
| `postgres` | `postgres:16.6` | 5432 → 5432 | system of record (`erp`, `audit`, `langgraph` schemas) |
| `qdrant` | `qdrant/qdrant:v1.12.4` | 6333 → 6333 | vector index (derived from Postgres) |
| `redis` | `redis:7.4` | 6379 → 6379 | disposable LLM-response cache |

## Start from a clean checkout

Prerequisites: Docker Desktop (Compose v2) and AWS credentials with **Amazon Bedrock access to Nova Lite and Titan Text Embeddings v2** in your chosen region.

```bash
# 1. Create env files from the templates (never commit the real ones)
cp .env.example .env
cp agent-service/.env.example         agent-service/.env
cp ai-auditor-services/.env.example   ai-auditor-services/.env
cp ai-auditor-app/.env.example        ai-auditor-app/.env
#    - set the SAME three database passwords in .env, agent-service/.env and ai-auditor-services/.env
#      (AGENT_DB_PASSWORD, SERVICES_DB_PASSWORD, POSTGRES_PASSWORD)
#    - put your AWS keys in agent-service/.env  (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_REGION_NAME)

# 2. One command
docker compose up --build -d
docker compose ps          # all six services should be "healthy"
```

Open **http://localhost:8501**. ERP/REST API docs: http://localhost:8001/docs · agent API docs: http://localhost:8000/docs.

Without AWS keys the agent refuses to start (by design: fail fast). To start the stack anyway for non-LLM work: `LLM_PROBE=off docker compose up --build -d`; extraction, validation and reporting then run with deterministic fallbacks, while translation, parsing quality, indexing, chat and RAGAS need Bedrock.

### Using Ollama instead of Bedrock (local dev)

Set the model lines in `agent-service/.env` to e.g. `LLM_REASONING_MODEL=ollama/qwen2.5:7b-instruct` (same for translation/generation) and `EMBEDDING_MODEL=ollama/bge-m3`, `EMBEDDING_DIM=1024`. `OLLAMA_BASE_URL` already points at the host. Changing the embedding model changes the vector size: call `POST http://localhost:8000/rag/reindex` after switching.

## Using it

1. **Upload Invoices** page (or drop a file into `data/incoming/`) — the agent picks it up within a few seconds.
2. **Dashboard** — counts, search and filters. Every audited invoice **waits for a human review** (status *Awaiting review*); open **Review report** to see the AI report, fix any value the system misread, and choose **Approve**, **Reject** or **Reject & revalidate**.
3. **Invoice Assistant** — chat about your invoices in plain language; answers show their sources and RAGAS quality scores once computed.

Demo files in `data/samples/`: `INV_EN_002_price_drift.docx` (one unit price 20% above the ERP → *review*) and `INV_ES_003_spanish.docx` (translated, clean). ERP master data loads from `infra/postgres/init/02_erp_schema.sql`; validation rules are in `config/rules.yaml`.

## How an invoice is persisted: exactly two business-data writes

| Step | What happens | PostgreSQL |
|---|---|---|
| raw-file checksum | file already audited? → duplicate event, stop | read |
| extract → translate | PDF: PyMuPDF text first, Tesseract only for pages without text; DOCX: python-docx; images: Tesseract | nothing |
| content checksum | `sha256(" ".join(translated_text.lower().split()))` already audited? → duplicate event, stop | read |
| **SQL Write #1** `ingest_translation` | one INSERT: paths, `file_checksum`, `content_checksum`, extraction method, raw + translated text, language, confidence | `audit.invoice_audit` (status `INGESTED`) |
| parse → validate (data + ERP) → report → index | state lives in the LangGraph checkpoint, report **files**, Qdrant, Redis | nothing |
| human review | `approve` / `reject` / `reject_and_revalidate` (max 3 re-validations; the 4th finalizes as `SKIPPED_MAX_RETRIES`) | review log only (below) |
| **SQL Write #2** `finalize_invoice` | ONE transaction: invoice fields, immutable `ai_audit_report`, line items, discrepancies (all runs), per-run reports, feedback not yet inserted, status, recommendation, `validation_run`, `human_check_count` | all-or-nothing |

`processing_events`, `human_feedback` and `review_decisions` are append-only *audit logs* written as things happen (a correction is inserted the moment it is submitted); they are not business data. Retries are idempotent (unique constraints + `ON CONFLICT DO NOTHING` + a row lock that makes a second finalization a no-op). The agent's DB role can only UPDATE the finalization columns and has no DELETE anywhere; `ai_audit_report` and the ingestion evidence are protected by a database trigger.

### Review API (agent-service)

```bash
# decision: approve | reject | reject_and_revalidate
curl -X POST http://localhost:8000/invoices/<invoice_id>/review -H 'content-type: application/json' -d '{
  "decision": "reject_and_revalidate",
  "reviewer": "auditor_jane",
  "comments": "PO number was misread",
  "field_corrections": [{"field_name": "po_number", "original_value": "PO-2026-884I", "corrected_value": "PO-2026-8841"}]
}'
# -> {"invoice_id": "...", "human_check_count": 1, "status": "NEEDS_REPORT_REVIEW", "validation_status": "failed",
#     "recommendation": "review", "discrepancies_count": 2, "validation_run": 2, "warnings": []}
```

Correctable fields: `invoice_number, vendor_name, po_number, invoice_date, currency, total_amount, line:<n>:<item_code|description|quantity|unit_price|line_total>`. Bad decisions/fields/values, missing lines or a blank reviewer return `4xx`. `GET /invoices/<id>/review` returns the live state; `POST /invoices/<id>/retry` resumes a run that stopped on a failing step. The original AI report stays available unchanged at `GET :8001/api/v1/invoices/<id>/ai-report` once finalized.

### RAG

The RAG code lives in `agent-service/app/agents/rag/` (one file per agent) and `app/graphs/rag_graph.py`:

```
router -> sql -> END
       -> retrieve -> augment -> generate -> reflect -> END
             ^                                 |
             +------------- retry <------------+   (a RAGAS score below its threshold, at most RAG_MAX_RETRIES)
```

- **router / sql** — the LLM (`with_structured_output`) picks SQL or RAG and one of a fixed list of parameterised queries (the LLM never writes SQL). An invoice awaiting human review has no final values in PostgreSQL yet, so its question is answered from the AI report, with a note.
- **retrieve** — Qdrant search (`RAG_TOP_K`, +5 per retry), filtered to the selected invoice/vendor.
- **augment** — the LLM scores each chunk 0–10 (reranking without PyTorch/Hugging Face); the best `RAG_FINAL_CONTEXT_K` (4) become the context.
- **generate** — answers only from that context.
- **reflect / retry** — RAGAS (the `ragas` package, pinned): context relevance, faithfulness, answer relevance against `RAGAS_MIN_*` (0.7). RAGAS failures are recorded, not retried. `RAG_REFLECTION=off` skips it.

`POST /rag/query` → `{invoice_id, answer, cache_hit, ragas_metrics{context_relevance, faithfulness, answer_relevance, low_confidence_warning, state}, sources[{source_type, chunk_text, score, rerank_score}], route, eval_id, retry_count}`. RAGAS runs inside the graph, so the scores come back with the answer (`state` = `done`, or `failed` with the reason) and are stored in `audit.rag_evaluations`; `GET /rag/evaluations/<eval_id>` returns them again. The RAGAS judge is `LLM_GENERATION_MODEL`, which must reliably return JSON (Bedrock Nova Lite, or e.g. `ollama/qwen2.5:7b-instruct`); with `llama3.2` (3B) faithfulness and answer relevance cannot be scored and the evaluation ends as `failed`. Answers to open questions are cached in Redis under `rag:{invoice_id|all}:{sha256(normalised question)}` for `RAG_CACHE_TTL_S` (3600 s); submitting corrections deletes `rag:{invoice_id}:*` (and cross-invoice `rag:all:*`) with a `SCAN` loop *before* re-validation and logs the count. The generic LLM cache (`llm:*`) is separate.

### Observability (LangFuse, optional)

Off by default. To enable, set in `agent-service/.env`: `LANGFUSE_ENABLED=true`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`; to disable set `LANGFUSE_ENABLED=false`. Traces cover pipeline runs, every stage, review rounds, LLM calls (model, latency, token counts — never prompt/response text or invoice content), errors, and scores (translation/OCR confidence, validation outcome, RAGAS). All traces for one invoice share a trace id derived from the invoice id; JSON logs carry `correlation_id` (invoice id) and `request_id`. A LangFuse outage never affects processing.

### Health

`GET /health` (agent): `{"status":"healthy","dependencies":{"postgres":"connected","qdrant":"connected","redis":"connected","mock_erp":"reachable"}}`, `503` + `"unhealthy"` when any is down. `GET /ready` adds the LLM configuration and is what the agent's Docker healthcheck calls (healthy only if invoices can really be processed). ai-auditor-services: `/health` = `{"status":"healthy","dependencies":{"postgres":"connected","incoming_dir":"writable"}}`.

### Paths and volumes

| Path | Container | Written by | Read by |
|---|---|---|---|
| `data/incoming/` (bind mount) | `/data/incoming` | ai-auditor-services (uploads, atomic rename) | agent-service (read-only) |
| `reports` (named volume) | `/data/reports` | agent-service: `{invoice_id}.json/.html` (latest), `{invoice_id}.run{n}.json/.html` (per run) | ai-auditor-services (read-only) → the UI via the API |

Report file names come only from validated UUIDs, resolved paths must stay inside the reports directory, and HTML is served with `Content-Security-Policy: sandbox` and `X-Content-Type-Options: nosniff`.

### Upgrading an existing database

Fresh installs get the final schema from `infra/postgres/init/`. A database created by an earlier version needs migration 001 (adds `content_checksum`, `status`, `human_check_count`, `ai_audit_report`, review tables, RAGAS columns; backfills `content_checksum` from the stored translated text — it aborts, changing nothing, if any invoice has no translated text or two invoices collide):

```bash
docker compose up -d postgres
docker compose --profile migrate run --rm db-migrate      # one transaction; safe to re-run
docker compose up --build -d
```

### Tests

```bash
cd agent-service && .venv/bin/python -m pytest tests/unit          # no external services
# DB + workflow tests need a throw-away PostgreSQL: see agent-service/tests/README.md
cd ai-auditor-services && .venv/bin/python -m pytest               # same database, services role
cd ai-auditor-app && .venv/bin/python -m pytest                    # Streamlit AppTest, all backends mocked
```

## Verification checklist (from the brief)

| # | Check | Commands |
|---|---|---|
| 1 | Single-command startup | `docker compose down -v && docker compose up --build -d && docker compose ps` |
| 2 | End-to-end | upload an invoice → `INGESTED` row (Write #1), report files, vectors; approve in the UI → Write #2 (fields, line items, `ai_audit_report`) |
| 3 | Persistence | `docker compose down && docker compose up -d` → audit trail and RAG still there; the file is skipped (same checksum), not reprocessed |
| 4 | Deliberate loss | `docker compose down -v && docker compose up -d` → audit and RAG empty (`-v` deletes the named volumes) |
| 5 | No secrets in images | `docker history ai-auditor/agent-service:1.0.0 --no-trunc` (repeat for the other two) |
| 6 | Service-name resolution | `docker compose exec agent-service python -c "import socket;print([socket.gethostbyname(h) for h in ('postgres','qdrant','redis','mock-erp')])"` |

Useful: `docker compose logs -f agent-service` · `docker compose exec postgres psql -U postgres -d auditor`.

## Data placement

| Store | Holds | Survives `down` | Survives `down -v` |
|---|---|---|---|
| `data/incoming/` (bind mount) | original invoice files | yes | yes |
| `reports` volume | JSON + HTML reports | yes | no |
| `pgdata` volume | ERP master data, audit trail, LangGraph checkpoints | yes | no (re-seeded on first start) |
| `qdrant_data` volume | embeddings + chunk payloads (rebuildable: `POST /rag/reindex`) | yes | no |
| Redis | LLM cache | n/a | n/a |

Rules enforced in code and by database grants: invoice binaries never enter a database; the agent reaches ERP data only through the ERP HTTP API (its DB role has no access to the `erp` schema); human corrections are INSERT-only.

## Security notes

- Real `.env` files are git-ignored and excluded from every image; only `.env.example` templates are committed. **Do not submit or commit `.env` files.**
- All images use pinned bases, run as a non-root user, and log to stdout/stderr.
