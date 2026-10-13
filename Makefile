# Engineering Knowledge RAG — docker compose shortcuts.
# Usage: make help

COMPOSE   ?= docker compose
RUN       := $(COMPOSE) run --rm
API_URL   ?= http://api:8000
Q         ?= ¿De dónde obtiene payments-service la variable PAYMENTS_DB_URL en producción?
ARGS      ?=
MODES     ?= all
CONCURRENCY ?= 50
REQUESTS  ?= 200

.DEFAULT_GOAL := help
.PHONY: help env build up down restart qdrant ingest api logs ps ready ask ask-debug \
        eval eval-fast test test-k load-test shell clean clean-all

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: Q=\"question\" ARGS=\"--env staging --mode hybrid\" MODES=dense,hybrid"
	@echo "             CONCURRENCY=50 REQUESTS=200 WEB_CONCURRENCY=2"

# ---------------------------------------------------------------- setup

env: ## Create .env from .env.example (if it does not exist)
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo ".env created: fill in OPENAI_API_KEY")

build: ## Build the Docker image
	$(COMPOSE) build api

# ---------------------------------------------------------------- services

up: qdrant ingest api ## Full flow: Qdrant + ingestion + API

qdrant: ## Start Qdrant
	$(COMPOSE) up -d qdrant

ingest: qdrant ## Index docs/ into Qdrant and build BM25 (recreates the collection)
	$(RUN) ingest

api: ## Start the API (gunicorn + uvicorn workers) at http://localhost:8000
	$(COMPOSE) up -d api

restart: ## Restart the API (e.g. after re-ingesting, to reload BM25)
	$(COMPOSE) restart api

down: ## Stop and remove the containers (keeps the Qdrant volume)
	$(COMPOSE) down

logs: ## Follow the API logs
	$(COMPOSE) logs -f api

ps: ## Container status
	$(COMPOSE) ps

ready: ## Check the API readiness
	@curl -s http://localhost:8000/health/ready; echo

# ---------------------------------------------------------------- queries

ask: ## Ask via the CLI: make ask Q="..." [ARGS="--env staging"]
	$(RUN) api python main.py ask "$(Q)" $(ARGS)

ask-debug: ## Same as ask, showing per-stage rankings
	$(RUN) api python main.py ask "$(Q)" --debug $(ARGS)

# ---------------------------------------------------------------- quality

test: ## Run the test suite (no network or API keys)
	$(RUN) --no-deps tests

test-k: ## Run filtered tests: make test-k K=chunker
	$(RUN) --no-deps tests pytest -q -k "$(K)"

eval: qdrant ## Full evaluation: retrieval per mode + abstention + Ragas
	$(RUN) eval python main.py eval --modes $(MODES)

eval-fast: qdrant ## Evaluation without Ragas (no LLM-judge, cheaper)
	$(RUN) eval python main.py eval --modes $(MODES) --no-ragas

load-test: ## Load test against the running API
	$(RUN) --no-deps api python scripts/load_test.py --url $(API_URL) \
		--concurrency $(CONCURRENCY) --requests $(REQUESTS)

# ---------------------------------------------------------------- utilities

shell: ## Shell inside the app container
	$(RUN) --no-deps api bash

clean: ## Delete generated artifacts (BM25 index, eval results)
	rm -f data/bm25_index.pkl data/eval/results_*.json data/eval/latest_results.json

clean-all: clean ## clean + remove containers and the Qdrant volume
	$(COMPOSE) down -v
