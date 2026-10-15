# Engineering Knowledge RAG

An assistant for engineering teams that answers technical questions about the internal architectures, backend stacks (Django, FastAPI, PostgreSQL) and cloud infrastructure (AWS, GCP) of a fictional company, **Nimbus Labs**. Every answer cites the fragments of internal documentation that support it. When the documentation doesn't cover a question, the assistant says it doesn't have the information instead of making something up.

Built on:
- **LLM and embeddings:** OpenAI (`text-embedding-3-small`, `gpt-4o-mini`) through LangChain (`ChatOpenAI`, `OpenAIEmbeddings`).
- **Vector store:** Qdrant through `langchain-qdrant`.
- **Lexical search:** BM25.
- **Reranker:** `BAAI/bge-reranker-base`.
- **Evaluation:** Ragas.

**Hybrid LangChain approach.** LangChain handles generation (an LCEL chain: `ChatPromptTemplate | ChatOpenAI | StrOutputParser`), embeddings and the vector store. The stages where the standard components would lose guarantees stay custom:
- the code-aware chunker (never splits a code block mid-line);
- BM25 with an identifier-preserving tokenizer and metadata filters (`BM25Retriever` has no filters);
- the RRF fusion;
- the reranker that exposes scores for the abstention threshold (`CrossEncoderReranker` drops them);
- the per-stage timeouts with graceful degradation;
- the concurrency limits.

LangChain is pinned to 0.3 because Ragas 0.2 is not compatible with LangChain 1.x.

The API is FastAPI, built for high concurrency. Everything runs in Docker.

> **Language note:** the sample corpus in `docs/`, the golden set and the system prompt are in Spanish, so the example questions below are in Spanish too.

## Architecture

```
[docs/*.md] ─> [Chunking + Metadata] ─> [Embeddings] ─> [Qdrant] + [BM25]
                                                            │
[Question] ─> [Hybrid Search: dense ∥ BM25 ─> RRF, top-20] ─> [Cross-Encoder rerank, top-5]
                                                            │
                     [Context Construction (source + visible metadata)]
                                                            │
             [LLM with anti-hallucination prompt] ─> answer + [n] citations
                                                            │
             [Evaluation: Hit/Recall/MRR/nDCG per mode · abstention · Ragas]
```

| Concept | Implementation |
|---|---|
| Chunking | [chunker.py](src/rag/ingestion/chunker.py): splits by headings (with a breadcrumb). Code blocks are atomic units and are only split at line boundaries, closing and reopening the fence in each part. Prose uses a recursive token splitter of 400 tokens with 60 tokens of overlap. |
| Metadata | [metadata.py](src/rag/ingestion/metadata.py): `tech_stack`, `doc_type`, `environment`, `last_updated`, `filepath`, plus `section`, `content_type` and `language` on each chunk. |
| Embeddings | `OpenAIEmbeddings` (batched), wrapped by `CachedQueryEmbeddings` in [embeddings.py](src/rag/retrieval/embeddings.py) for a bounded TTL cache of query embeddings. |
| Vector store | [vector_store.py](src/rag/retrieval/vector_store.py): thin adapter over `langchain_qdrant.QdrantVectorStore` (cosine distance), plus payload indexes and metadata filters. |
| Similarity search | Dense cosine query, top-K, with metadata filters. |
| Hybrid search | [hybrid.py](src/rag/retrieval/hybrid.py): dense and BM25 search run in parallel and are merged with Reciprocal Rank Fusion. The tokenizer in [lexical.py](src/rag/retrieval/lexical.py) keeps identifiers such as `PAYMENTS_DB_URL` or `/api/v1/payments/refunds` intact. |
| Retrieval and reranking | [reranker.py](src/rag/retrieval/reranker.py) and [pipeline.py](src/rag/generation/pipeline.py): the top-K=20 candidates go to the cross-encoder, which keeps the top-N=5. If the best score falls below a threshold, the system abstains without calling the LLM. |
| Context construction | [context_builder.py](src/rag/generation/context_builder.py): `[n] Fuente: … § … \| stack \| tipo \| entorno \| actualizado` blocks, with deduplication and a token budget. |
| Anti-hallucination | [prompts.py](src/rag/generation/prompts.py) and [llm.py](src/rag/generation/llm.py): an LCEL chain over a `ChatPromptTemplate`; only the context may be used, every claim carries an `[n]` citation, abstention uses a fixed message, and conflicts are resolved by date and environment. |
| Retrieval evaluation | [evaluation/](src/rag/evaluation/): Hit@5, Recall@5/20, Precision@5, MRR and nDCG@5 per mode; abstention rate; and Ragas (Context Precision, Context Recall, Faithfulness, Answer Relevancy). |

## Quick start (Docker)

```bash
cp .env.example .env          # set OPENAI_API_KEY
docker compose build
docker compose up -d qdrant
docker compose run --rm ingest                 # index docs/ into Qdrant + BM25
docker compose up -d api                       # API at http://localhost:8000 (docs at /docs)
```

`make` offers shortcuts for the same steps; `make help` lists every target:

```bash
make env && make build && make up              # .env, image, Qdrant + ingestion + API
make ask Q="¿Cuánto vale PAYMENTS_DB_POOL_SIZE?" ARGS="--env staging"
make test | make eval | make eval-fast | make load-test | make down
```

### Queries

```bash
# CLI
docker compose run --rm api python main.py ask "¿De dónde obtiene payments-service PAYMENTS_DB_URL en producción?" --debug
docker compose run --rm api python main.py ask "¿Cuánto vale PAYMENTS_DB_POOL_SIZE?" --env staging
docker compose run --rm api python main.py ask "¿Cómo se configura Redis?"      # -> abstention

# API
curl -X POST http://localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "¿Qué scope requiere POST /api/v1/payments/refunds?", "filters": {"tech_stack": ["fastapi"]}, "debug": true}'
```

`mode` accepts `dense`, `bm25`, `hybrid` or `hybrid_rerank` (the default). The available filters are `tech_stack` (matches if at least one value is shared), `environment` (always includes documents marked `all`) and `doc_type`.

### Tests, evaluation and load

```bash
docker compose run --rm --no-deps tests        # pytest, no network or API keys needed
docker compose run --rm eval                   # compare modes + Ragas -> data/eval/results_*.json
docker compose run --rm api python scripts/load_test.py --url http://api:8000 --concurrency 50 --requests 200
```

The [notebooks/evaluation.ipynb](notebooks/evaluation.ipynb) notebook plots `data/eval/latest_results.json`.

## API and concurrency

| Endpoint | Description |
|---|---|
| `POST /ask` | `{question, filters?, mode?, debug?}` → `{answer, abstained, citations[], latency_ms, request_id, debug?}` |
| `GET /health` | Liveness: checks that the process responds, without touching external dependencies. |
| `GET /health/ready` | Readiness: checks the points indexed in Qdrant, the BM25 index and the reranker. Returns `ok`, `degraded` or `503`. |

API design:
- **[lifespan.py](src/rag/api/lifespan.py)** initializes resources once per worker, through [resources.py](src/rag/resources.py):
  - `ChatOpenAI` and `OpenAIEmbeddings` sharing one `httpx.AsyncClient` connection pool.
  - The Qdrant client and the `langchain-qdrant` vector store.
  - The in-memory, read-only BM25 index.
  - The cross-encoder, warmed up with a dummy inference.
  - The concurrency limiters.

  On shutdown, it closes everything in order with `AsyncExitStack`.
- **[depends.py](src/rag/api/depends.py)** exposes those resources through `Depends` without creating objects per request, so they can be swapped with `dependency_overrides`. It includes `concurrency_guard`, which applies backpressure: with more than `MAX_IN_FLIGHT_REQUESTS` requests in flight it returns **503 with `Retry-After`** instead of queuing without limit.
- **Non-blocking event loop:**
  - OpenAI calls (LLM and query embeddings) are async.
  - `langchain-qdrant` has no async API, so Qdrant calls run in threads behind their own `CapacityLimiter` (`QDRANT_CONCURRENCY`).
  - BM25 and the reranker (CPU-bound) run in threads behind a `CapacityLimiter`.
  - LLM calls are bounded by a semaphore to respect rate limits.
- **Per-stage timeouts.** If the reranker exceeds its budget, the system falls back to the RRF order and flags it in `debug`. If retrieval or the LLM times out, the response is a **504** with the standard error body.
- **Pure ASGI middleware** for `X-Request-ID`, `X-Process-Time-ms` and the JSON access log.
- **Server:** gunicorn with `uvicorn_worker.UvicornWorker` workers. Configured through `WEB_CONCURRENCY` (2 by default, since each worker loads the ~1 GB reranker), `max_requests` to recycle workers, and a graceful timeout. See [gunicorn.conf.py](gunicorn.conf.py).

## Layout

```
docs/                  13 fictional Markdown guides (architecture, Django/FastAPI, PostgreSQL, AWS/GCP, runbooks)
data/eval/             golden_set.jsonl (28 questions, 4 unanswerable) and results
src/rag/
  ingestion/           loader, metadata, chunker, indexer
  retrieval/           embeddings, vector_store, lexical (BM25), hybrid (RRF), reranker
  generation/          prompts, context_builder, llm, pipeline
  evaluation/          golden_set, retrieval_metrics, ragas_eval, run_eval
  api/                 app, lifespan, depends, middleware, schemas, routers/
  resources.py         composition root shared by the API, CLI and evaluation
tests/                 unit + API + concurrency tests (deterministic fakes, in-memory Qdrant)
scripts/load_test.py   async load test
main.py                CLI: ingest | ask | eval | serve
Makefile               shortcuts over docker compose
```

## Configuration

All options live in [config.py](src/rag/config.py) and can be overridden with environment variables (see [.env.example](.env.example)). The main ones are:
- **Retrieval:** `TOP_K`, `TOP_N`, `MIN_RERANK_SCORE`.
- **Chunking:** `CHUNK_SIZE_TOKENS`, `CHUNK_OVERLAP_TOKENS`.
- **Concurrency:** `MAX_IN_FLIGHT_REQUESTS`, `LLM_CONCURRENCY`, `RERANK_CONCURRENCY`, `TORCH_THREADS`.
- **Timeouts:** `RETRIEVAL_TIMEOUT_S`, `RERANK_TIMEOUT_S`, `LLM_TIMEOUT_S`.
