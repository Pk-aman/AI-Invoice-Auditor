# AI Invoice Auditor

A containerized, multilingual invoice-validation system. Invoices (PDF / DOCX / scanned image) are ingested from a watched folder, extracted (with OCR), translated to English, validated against a mock ERP, reported, indexed for RAG, and reviewed by a human auditor in a Streamlit app.

Design docs: [Plan.md](Plan.md) (high level) · [agent-service/Plan.md](agent-service/Plan.md) · [ai-auditor-services/Plan.md](ai-auditor-services/Plan.md) · [ai-auditor-app/Plan.md](ai-auditor-app/Plan.md)

## Services

| Compose service | Directory | Host port → container | Role |
|---|---|---|---|
| `ai-auditor-app` (alias `streamlit-ui`) | `ai-auditor-app/` | 8501 → 8501 | Streamlit UI: Dashboard · Upload Invoice · Invoice Assistant |
| `ai-auditor-services` (alias `mock-erp`) | `ai-auditor-services/` | 8001 → 8000 | FastAPI: mock ERP + REST API for invoices, uploads, feedback, stats |
| `agent-service` (alias `agent`) | `agent-service/` | 8000 → 8000 | LangGraph pipeline, OCR, translation, validation, reporting, RAG |
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

Without AWS keys the agent refuses to start (by design: fail fast). To start the stack anyway for non-LLM work: `LLM_PROBE=off docker compose up --build -d`; extraction, validation and reporting then run with deterministic fallbacks, while translation, parsing quality, indexing and chat need Bedrock.

### Using Ollama instead of Bedrock (local dev)

Set the model lines in `agent-service/.env` to e.g. `LLM_REASONING_MODEL=ollama/qwen2.5:7b-instruct` (same for translation/generation) and `EMBEDDING_MODEL=ollama/bge-m3`, `EMBEDDING_DIM=1024`. `OLLAMA_BASE_URL` already points at the host. Changing the embedding model changes the vector size: call `POST http://localhost:8000/rag/reindex` after switching.

## Using it

1. **Upload Invoices** page (or drop a file into `data/incoming/`) — the agent picks it up within a few seconds.
2. **Dashboard** — invoice counts, search and filters (status, audit result, upload date), and a **View report** dialog per invoice with the audit findings and the original document.
3. **Invoice Assistant** — chat about your invoices in plain language ("Which invoices need attention?"); optionally scope a question to one invoice.

Demo files: `INV_EN_002_price_drift.docx` → **review** (one unit price 20% above the ERP); `INV_ES_003_spanish.docx` → translated and **approved**. ERP master data is loaded automatically from `infra/postgres/init/02_erp_schema.sql`; validation rules are in `config/rules.yaml`. Two ready-made demo invoices (a price mismatch and a Spanish one) are in `data/samples/`.

## Verification checklist (from the brief)

| # | Check | Commands |
|---|---|---|
| 1 | Single-command startup | `docker compose down -v && docker compose up --build -d && docker compose ps` |
| 2 | End-to-end | upload an invoice → rows in `audit.*`, report in the `reports` volume, vectors in Qdrant |
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
