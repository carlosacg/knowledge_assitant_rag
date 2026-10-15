"""Hybrid search: dense (Qdrant) + lexical (BM25) fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

import anyio

from rag.models import ScoredChunk, SearchFilters
from rag.retrieval.lexical import BM25Index
from rag.retrieval.vector_store import QdrantVectorStore


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[ScoredChunk]], k: int = 60, top_k: int | None = None
) -> list[ScoredChunk]:
    """RRF: score(d) = Σ_r 1 / (k + rank_r(d)). No need to normalize heterogeneous scores."""
    fused: dict[str, ScoredChunk] = {}
    for name, ranking in rankings.items():
        for rank, item in enumerate(ranking, start=1):
            entry = fused.setdefault(item.chunk_id, ScoredChunk(item.chunk, 0.0, {}))
            entry.score += 1.0 / (k + rank)
            entry.scores[name] = item.score
            entry.scores[f"{name}_rank"] = rank
    ordered = sorted(fused.values(), key=lambda e: e.score, reverse=True)
    for entry in ordered:
        entry.scores["rrf"] = entry.score
    return ordered[:top_k] if top_k else ordered


@dataclass
class RetrievalResult:
    candidates: list[ScoredChunk]
    debug: dict[str, Any] = field(default_factory=dict)


class HybridRetriever:
    def __init__(
        self,
        vector_store: QdrantVectorStore,
        bm25: BM25Index | None,
        cpu_limiter: anyio.CapacityLimiter | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.vector_store = vector_store
        self.bm25 = bm25
        self.cpu_limiter = cpu_limiter
        self.rrf_k = rrf_k

    async def dense_search(self, query: str, top_k: int, filters: SearchFilters | None) -> list[ScoredChunk]:
        return await self.vector_store.search(query, top_k, filters)

    async def lexical_search(self, query: str, top_k: int, filters: SearchFilters | None) -> list[ScoredChunk]:
        if self.bm25 is None:
            return []
        # BM25 is CPU-bound: run it off the event loop.
        return await anyio.to_thread.run_sync(
            partial(self.bm25.search, query, top_k, filters), limiter=self.cpu_limiter
        )

    async def retrieve(
        self, query: str, mode: str, top_k: int, filters: SearchFilters | None = None
    ) -> RetrievalResult:
        if mode == "dense":
            dense = await self.dense_search(query, top_k, filters)
            return RetrievalResult(dense, {"dense": trace_ranking(dense)})
        if mode == "bm25":
            lexical = await self.lexical_search(query, top_k, filters)
            return RetrievalResult(lexical, {"bm25": trace_ranking(lexical)})
        if mode != "hybrid":
            raise ValueError(f"Modo de recuperación desconocido: {mode}")

        dense, lexical = await asyncio.gather(
            self.dense_search(query, top_k, filters),
            self.lexical_search(query, top_k, filters),
        )
        fused = reciprocal_rank_fusion({"dense": dense, "bm25": lexical}, k=self.rrf_k, top_k=top_k)
        debug: dict[str, Any] = {
            "dense": trace_ranking(dense),
            "bm25": trace_ranking(lexical),
            "rrf": trace_ranking(fused),
        }
        if self.bm25 is None:
            debug["warning"] = "índice BM25 no disponible: búsqueda solo densa"
        return RetrievalResult(fused, debug)


def trace_ranking(items: Sequence[ScoredChunk], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "rank": i,
            "chunk_id": it.chunk_id,
            "filepath": it.chunk.metadata.get("filepath"),
            "section": it.chunk.metadata.get("section"),
            "score": round(it.score, 5),
        }
        for i, it in enumerate(items[:limit], start=1)
    ]
