.PHONY: venv seed infra-up infra-down reset ps psql

venv:        ## local dev virtualenv at ./.venv
	python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip

seed:        ## regenerate ERP seed SQL from Raw Data/data/ERP_mockdata
	python3 ai-auditor-services/scripts/gen_erp_seed.py

infra-up:    ## start postgres, qdrant, redis and wait until healthy
	docker compose up -d --wait postgres qdrant redis

infra-down:  ## stop containers, KEEP volumes
	docker compose down

reset:       ## DESTROY volumes and start clean (re-runs the init SQL)
	docker compose down -v
	docker compose up -d --wait postgres qdrant redis

ps:
	docker compose ps

psql:
	docker compose exec postgres psql -U postgres -d auditor
