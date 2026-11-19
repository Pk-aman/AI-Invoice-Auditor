# agent-service tests

```bash
# unit tests only (no external services)
.venv/bin/python -m pytest tests/unit

# + database tests: start a throw-away PostgreSQL initialised from the repo's init scripts
docker run -d --name auditor-test-pg -e POSTGRES_PASSWORD=pgadmin -e POSTGRES_DB=auditor \
  -e AGENT_DB_PASSWORD=agentpw -e SERVICES_DB_PASSWORD=svcpw -p 55432:5432 \
  -v "$PWD/../infra/postgres/init:/docker-entrypoint-initdb.d:ro" postgres:16.6
TEST_ADMIN_DATABASE_URL=postgresql://postgres:pgadmin@localhost:55432/auditor \
TEST_DATABASE_URL=postgresql://agent_rw:agentpw@localhost:55432/auditor \
  .venv/bin/python -m pytest
```
The LLM, AWS Bedrock, Qdrant (in-memory client), Redis (fakeredis) and the ERP are all test doubles.
