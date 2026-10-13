# Engineering Knowledge RAG

Asistente para equipos de ingeniería que responde dudas técnicas sobre las arquitecturas internas, los stacks backend (Django, FastAPI, PostgreSQL) y la infraestructura cloud (AWS, GCP) de una empresa ficticia, **Nimbus Labs**. Cada respuesta cita los fragmentos de la documentación interna que la respaldan. Si la documentación no cubre la pregunta, el asistente responde que no cuenta con la información en lugar de inventarla.

Implementación nativa, sin LangChain ni LlamaIndex en el pipeline, sobre:
- **LLM y embeddings:** OpenAI (`text-embedding-3-small`, `gpt-4o-mini`).
- **Vector store:** Qdrant.
- **Búsqueda léxica:** BM25.
- **Reranker:** `BAAI/bge-reranker-base`.
- **Evaluación:** Ragas.

La API es FastAPI y está preparada para alta concurrencia. Todo corre en Docker.

## Arquitectura

```
[docs/*.md] ─> [Chunking + Metadata] ─> [Embeddings] ─> [Qdrant] + [BM25]
                                                            │
[Pregunta] ─> [Hybrid Search: dense ∥ BM25 ─> RRF, top-20] ─> [Cross-Encoder rerank, top-5]
                                                            │
                     [Context Construction (fuente + metadata visibles)]
                                                            │
             [LLM con prompt anti-alucinación] ─> respuesta + citas [n]
                                                            │
             [Evaluación: Hit/Recall/MRR/nDCG por modo · abstención · Ragas]
```

| Concepto | Implementación |
|---|---|
| Chunking | [chunker.py](src/rag/ingestion/chunker.py): se divide por encabezados (con breadcrumb). Los bloques de código son unidades atómicas y solo se cortan por líneas, cerrando y reabriendo el fence en cada parte. La prosa usa un splitter recursivo por tokens de 400, con 60 de solapamiento. |
| Metadata | [metadata.py](src/rag/ingestion/metadata.py): `tech_stack`, `doc_type`, `environment`, `last_updated`, `filepath`, más `section`, `content_type` y `language` en cada chunk. |
| Embeddings | [embeddings.py](src/rag/retrieval/embeddings.py): batches en paralelo y caché TTL para los embeddings de las consultas. |
| Vector store | [vector_store.py](src/rag/retrieval/vector_store.py): Qdrant con distancia coseno e índices de payload para filtrar. |
| Similarity search | Consulta densa coseno, top-K, con filtros por metadata. |
| Hybrid search | [hybrid.py](src/rag/retrieval/hybrid.py): búsqueda densa y BM25 en paralelo, fusionadas con Reciprocal Rank Fusion. El tokenizer de [lexical.py](src/rag/retrieval/lexical.py) preserva identificadores como `PAYMENTS_DB_URL` o `/api/v1/payments/refunds`. |
| Retrieval y reranking | [reranker.py](src/rag/retrieval/reranker.py) y [pipeline.py](src/rag/generation/pipeline.py): Top-K=20 pasa al cross-encoder, que deja Top-N=5. Si el mejor score queda bajo un umbral, el sistema se abstiene sin llamar al LLM. |
| Context construction | [context_builder.py](src/rag/generation/context_builder.py): bloques `[n] Fuente: … § … \| stack \| tipo \| entorno \| actualizado`, con deduplicación y presupuesto de tokens. |
| Anti-hallucination | [prompts.py](src/rag/generation/prompts.py): solo se usa el contexto, cada afirmación lleva cita `[n]`, la abstención usa un texto literal, y los conflictos se resuelven por fecha y entorno. |
| Retrieval evaluation | [evaluation/](src/rag/evaluation/): Hit@5, Recall@5/20, Precision@5, MRR y nDCG@5 por modo; tasa de abstención; y Ragas (Context Precision, Context Recall, Faithfulness, Answer Relevancy). |

## Inicio rápido (Docker)

```bash
cp .env.example .env          # completar OPENAI_API_KEY
docker compose build
docker compose up -d qdrant
docker compose run --rm ingest                 # indexa docs/ en Qdrant + BM25
docker compose up -d api                       # API en http://localhost:8000 (docs en /docs)
```

Con `make` puedes hacer lo mismo de forma abreviada; `make help` lista todos los comandos:

```bash
make env && make build && make up              # .env, imagen, Qdrant + ingesta + API
make ask Q="¿Cuánto vale PAYMENTS_DB_POOL_SIZE?" ARGS="--env staging"
make test | make eval | make eval-fast | make load-test | make down
```

### Consultas

```bash
# CLI
docker compose run --rm api python main.py ask "¿De dónde obtiene payments-service PAYMENTS_DB_URL en producción?" --debug
docker compose run --rm api python main.py ask "¿Cuánto vale PAYMENTS_DB_POOL_SIZE?" --env staging
docker compose run --rm api python main.py ask "¿Cómo se configura Redis?"      # -> abstención

# API
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "¿Qué scope requiere POST /api/v1/payments/refunds?", "filters": {"tech_stack": ["fastapi"]}, "debug": true}'
```

`mode` puede tomar los valores `dense`, `bm25`, `hybrid` o `hybrid_rerank` (por defecto). Los filtros disponibles son `tech_stack` (coincide si comparte al menos un valor), `environment` (siempre incluye los documentos marcados `all`) y `doc_type`.

### Tests, evaluación y carga

```bash
docker compose run --rm tests                  # pytest, sin red ni API keys
docker compose run --rm eval                   # compara modos + Ragas -> data/eval/results_*.json
docker compose run --rm api python scripts/load_test.py --url http://api:8000 --concurrency 50 --requests 200
```

El notebook [notebooks/evaluation.ipynb](notebooks/evaluation.ipynb) grafica `data/eval/latest_results.json`.

## API y concurrencia

| Endpoint | Descripción |
|---|---|
| `POST /ask` | `{question, filters?, mode?, debug?}` → `{answer, abstained, citations[], latency_ms, request_id, debug?}` |
| `GET /health` | Liveness: comprueba que el proceso responde, sin consultar dependencias externas. |
| `GET /health/ready` | Readiness: comprueba los puntos indexados en Qdrant, el índice BM25 y el reranker. Devuelve `ok`, `degraded` o `503`. |

Diseño de la API:
- **[lifespan.py](src/rag/api/lifespan.py)** inicializa los recursos una vez por worker, a través de [resources.py](src/rag/resources.py):
  - `AsyncOpenAI` sobre un `httpx.AsyncClient` con pool de conexiones.
  - `AsyncQdrantClient`.
  - El índice BM25 en memoria, de solo lectura.
  - El cross-encoder, precalentado con una inferencia de prueba.
  - Los limitadores de concurrencia.

  Al apagar, cierra todo de forma ordenada con `AsyncExitStack`.
- **[depends.py](src/rag/api/depends.py)** expone esos recursos mediante `Depends`, sin crear objetos por request, así que se pueden reemplazar con `dependency_overrides`. Incluye `concurrency_guard`, que aplica backpressure: con más de `MAX_IN_FLIGHT_REQUESTS` solicitudes en curso responde **503 con `Retry-After`** en vez de encolar sin límite.
- **Event loop sin bloqueos:**
  - Todo el I/O es async.
  - BM25 y el reranker (CPU-bound) corren en threads con `CapacityLimiter`.
  - Las llamadas al LLM se limitan con un semáforo para respetar los rate limits.
- **Timeouts por etapa.** Si el reranker se excede, el sistema usa el orden RRF y lo indica en `debug`. Si se excede el retrieval o el LLM, la respuesta es **504** con el cuerpo de error estándar.
- **Middleware ASGI puro** para `X-Request-ID`, `X-Process-Time-ms` y el log de acceso en JSON.
- **Servidor:** gunicorn con workers `uvicorn_worker.UvicornWorker`. Se configura con `WEB_CONCURRENCY` (por defecto 2, porque cada worker carga el reranker de ~1 GB), `max_requests` para reciclar workers y un graceful timeout. Ver [gunicorn.conf.py](gunicorn.conf.py).

## Estructura

```
docs/                  13 guías Markdown ficticias (arquitectura, Django/FastAPI, PostgreSQL, AWS/GCP, runbooks)
data/eval/             golden_set.jsonl (28 preguntas, 4 sin respuesta posible) y resultados
src/rag/
  ingestion/           loader, metadata, chunker, indexer
  retrieval/           embeddings, vector_store, lexical (BM25), hybrid (RRF), reranker
  generation/          prompts, context_builder, llm, pipeline
  evaluation/          golden_set, retrieval_metrics, ragas_eval, run_eval
  api/                 app, lifespan, depends, middleware, schemas, routers/
  resources.py         composition root compartido por API, CLI y evaluación
tests/                 unitarios + API + concurrencia (fakes deterministas, Qdrant en memoria)
scripts/load_test.py   prueba de carga async
main.py                CLI: ingest | ask | eval | serve
```

## Configuración

Todas las opciones están en [config.py](src/rag/config.py) y se sobreescriben con variables de entorno (ver [.env.example](.env.example)). Las principales son:
- **Recuperación:** `TOP_K`, `TOP_N`, `MIN_RERANK_SCORE`.
- **Chunking:** `CHUNK_SIZE_TOKENS`, `CHUNK_OVERLAP_TOKENS`.
- **Concurrencia:** `MAX_IN_FLIGHT_REQUESTS`, `LLM_CONCURRENCY`, `RERANK_CONCURRENCY`, `TORCH_THREADS`.
- **Timeouts:** `RETRIEVAL_TIMEOUT_S`, `RERANK_TIMEOUT_S`, `LLM_TIMEOUT_S`.
