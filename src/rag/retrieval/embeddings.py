"""Dense embedding generation (OpenAI) with batching and a query cache."""

from __future__ import annotations

import asyncio
from typing import Protocol

from cachetools import TTLCache
from openai import AsyncOpenAI


class Embedder(Protocol):
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        dim: int,
        batch_size: int = 128,
        max_parallel_batches: int = 4,
        query_cache: TTLCache | None = None,
    ) -> None:
        self._client = client
        self.model = model
        self.dim = dim
        self._batch_size = batch_size
        self._parallel = asyncio.Semaphore(max_parallel_batches)
        # Repeated queries are common (technical FAQs): avoid paying for the round-trip.
        self._cache = query_cache

    async def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        async with self._parallel:
            response = await self._client.embeddings.create(model=self.model, input=batch)
        return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        batches = [texts[i : i + self._batch_size] for i in range(0, len(texts), self._batch_size)]
        results = await asyncio.gather(*(self._embed_batch(b) for b in batches))
        return [vector for batch in results for vector in batch]

    async def embed_query(self, text: str) -> list[float]:
        key = text.strip()
        if self._cache is not None and (cached := self._cache.get(key)) is not None:
            return cached
        [vector] = await self._embed_batch([key])
        if self._cache is not None:
            self._cache[key] = vector
        return vector
