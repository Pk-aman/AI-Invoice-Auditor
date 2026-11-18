#!/bin/bash
# Runs once, on first start of an empty data volume (docker-entrypoint-initdb.d).
# Passwords come from the container environment (env_file), never from SQL files in git.
set -euo pipefail
: "${AGENT_DB_PASSWORD:?AGENT_DB_PASSWORD is required}"
: "${SERVICES_DB_PASSWORD:?SERVICES_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 -v agent_pw="$AGENT_DB_PASSWORD" -v services_pw="$SERVICES_DB_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pg_trgm;          -- fuzzy vendor-name search (ERP)

-- Least-privilege application roles (one per service)
CREATE ROLE agent_rw    LOGIN PASSWORD :'agent_pw';
CREATE ROLE services_rw LOGIN PASSWORD :'services_pw';

-- Three schemas, one database
CREATE SCHEMA erp;                                -- owner: mock ERP data (ai-auditor-services reads)
CREATE SCHEMA audit;                              -- audit trail
CREATE SCHEMA langgraph AUTHORIZATION agent_rw;   -- LangGraph checkpointer creates its own tables here

REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA erp   TO services_rw;       -- agent_rw deliberately has NO access to erp
GRANT USAGE ON SCHEMA audit TO agent_rw, services_rw;
SQL
