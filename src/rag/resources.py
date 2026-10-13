"""System dependency wiring (composition root).

Used by the FastAPI ``lifespan`` (one instance per worker), the CLI and the evaluation, so
they all run exactly the same pipeline.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

import anyio
from cachetools import TTLCache

from rag.clients import create_http_client, create_openai_client, create_qdrant_client
from rag.config import Settings
from rag.generation.llm import OpenAIChatLLM
from rag.generation.pipeline import RAGPipeline
from rag.retrieval.embeddings import OpenAIEmbedder
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.lexical import BM25Index
from rag.retrieval.reranker import CrossEncoderReranker
from rag.retrieval.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


class InFlightLimiter:
    """Non-blocking per-worker limit on in-flight requests (backpressure -> 503).

    Each worker has a single event loop, so the counter needs no locks.
    """

    def __init__(self, max_in_flight: int) -> None:
        self.max_in_flight = max_in_flight
        self._current = 0

    @property
    def in_flight(self) -> int:
        return self._current

    def try_acquire(self) -> bool:
        if self._current >= self.max_in_flight:
            return False
        self._current += 1
        return True

    def release(self) -> None:
        self._current = max(0, self._current - 1)


@dataclass
class AppResources:
    settings: Settings
    pipeline: RAGPipeline
    vector_store: QdrantVectorStore
    bm25: BM25Index | None
    reranker: CrossEncoderReranker | None
    request_limiter: InFlightLimiter


def load_bm25(settings: Settings) -> BM25Index | None:
    if not settings.bm25_path.exists():
        logger.warning("índice BM25 no encontrado; ejecutar 'ingest'", extra={"path": str(settings.bm25_path)})
        return None
    return BM25Index.load(settings.bm25_path)


@asynccontextmanager
async def open_resources(settings: Settings, *, load_reranker: bool | None = None) -> AsyncIterator[AppResources]:
    """Creates clients, models and indexes (once) and releases them on exit."""
    async with AsyncExitStack() as stack:
        http_client = create_http_client(settings)
        stack.push_async_callback(http_client.aclose)
        openai_client = create_openai_client(settings, http_client)
        stack.push_async_callback(openai_client.close)
        qdrant = create_qdrant_client(settings)
        stack.push_async_callback(qdrant.close)

        embedder = OpenAIEmbedder(
            openai_client,
            settings.embedding_model,
            settings.embedding_dim,
            batch_size=settings.embedding_batch_size,
            query_cache=TTLCache(maxsize=settings.embedding_cache_size, ttl=settings.embedding_cache_ttl_s),
        )
        vector_store = QdrantVectorStore(qdrant, settings.qdrant_collection, settings.embedding_dim)
        bm25 = await anyio.to_thread.run_sync(load_bm25, settings)

        reranker: CrossEncoderReranker | None = None
        if settings.reranker_enabled if load_reranker is None else load_reranker:
            reranker = CrossEncoderReranker(
                settings.reranker_model,
                max_length=settings.reranker_max_length,
                batch_size=settings.reranker_batch_size,
                torch_threads=settings.torch_threads,
            )
            # Load + dummy inference at startup: the first request does not pay the latency.
            await anyio.to_thread.run_sync(reranker.warmup)

        retriever = HybridRetriever(
            embedder,
            vector_store,
            bm25,
            cpu_limiter=anyio.CapacityLimiter(settings.cpu_concurrency),
            rrf_k=settings.rrf_k,
        )
        llm = OpenAIChatLLM(
            openai_client,
            settings.llm_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            max_concurrency=settings.llm_concurrency,
        )
        pipeline = RAGPipeline(
            retriever,
            llm,
            settings,
            reranker=reranker,
            rerank_limiter=anyio.CapacityLimiter(settings.rerank_concurrency),
        )
        logger.info(
            "recursos inicializados",
            extra={"bm25_chunks": len(bm25) if bm25 else 0, "reranker": bool(reranker)},
        )
        yield AppResources(
            settings=settings,
            pipeline=pipeline,
            vector_store=vector_store,
            bm25=bm25,
            reranker=reranker,
            request_limiter=InFlightLimiter(settings.max_in_flight_requests),
        )
