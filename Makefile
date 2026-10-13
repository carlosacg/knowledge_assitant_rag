# Engineering Knowledge RAG — atajos sobre docker compose.
# Uso: make help

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

help: ## Muestra esta ayuda
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: Q=\"pregunta\" ARGS=\"--env staging --mode hybrid\" MODES=dense,hybrid"
	@echo "             CONCURRENCY=50 REQUESTS=200 WEB_CONCURRENCY=2"

# ---------------------------------------------------------------- setup

env: ## Crea .env a partir de .env.example (si no existe)
	@test -f .env && echo ".env ya existe" || (cp .env.example .env && echo ".env creado: completar OPENAI_API_KEY")

build: ## Construye la imagen Docker
	$(COMPOSE) build api

# ---------------------------------------------------------------- servicios

up: qdrant ingest api ## Flujo completo: Qdrant + ingesta + API

qdrant: ## Levanta Qdrant
	$(COMPOSE) up -d qdrant

ingest: qdrant ## Indexa docs/ en Qdrant y construye BM25 (recrea la colección)
	$(RUN) ingest

api: ## Levanta la API (gunicorn + uvicorn workers) en http://localhost:8000
	$(COMPOSE) up -d api

restart: ## Reinicia la API (p. ej. tras re-ingestar, para recargar BM25)
	$(COMPOSE) restart api

down: ## Detiene y elimina los contenedores (conserva el volumen de Qdrant)
	$(COMPOSE) down

logs: ## Sigue los logs de la API
	$(COMPOSE) logs -f api

ps: ## Estado de los contenedores
	$(COMPOSE) ps

ready: ## Consulta el readiness de la API
	@curl -s http://localhost:8000/health/ready; echo

# ---------------------------------------------------------------- consultas

ask: ## Pregunta por CLI: make ask Q="..." [ARGS="--env staging"]
	$(RUN) api python main.py ask "$(Q)" $(ARGS)

ask-debug: ## Igual que ask, mostrando los rankings por etapa
	$(RUN) api python main.py ask "$(Q)" --debug $(ARGS)

# ---------------------------------------------------------------- calidad

test: ## Corre la suite de tests (sin red ni API keys)
	$(RUN) --no-deps tests

test-k: ## Corre tests filtrados: make test-k K=chunker
	$(RUN) --no-deps tests pytest -q -k "$(K)"

eval: qdrant ## Evaluación completa: retrieval por modo + abstención + Ragas
	$(RUN) eval python main.py eval --modes $(MODES)

eval-fast: qdrant ## Evaluación sin Ragas (sin LLM-judge, más barata)
	$(RUN) eval python main.py eval --modes $(MODES) --no-ragas

load-test: ## Prueba de carga contra la API levantada
	$(RUN) --no-deps api python scripts/load_test.py --url $(API_URL) \
		--concurrency $(CONCURRENCY) --requests $(REQUESTS)

# ---------------------------------------------------------------- utilidades

shell: ## Shell dentro del contenedor de la app
	$(RUN) --no-deps api bash

clean: ## Borra artefactos generados (índice BM25, resultados de eval)
	rm -f data/bm25_index.pkl data/eval/results_*.json data/eval/latest_results.json

clean-all: clean ## clean + elimina contenedores y el volumen de Qdrant
	$(COMPOSE) down -v
