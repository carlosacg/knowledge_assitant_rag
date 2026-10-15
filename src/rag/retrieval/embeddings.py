"""Dense embeddings: any LangChain ``Embeddings`` (OpenAIEmbeddings in production) plus a query cache."""

from __future__ import annotations

import threading

from cachetools import TTLCache
from langchain_core.embeddings import Embeddings


class CachedQueryEmbeddings(Embeddings):
    """Wraps an ``Embeddings`` and caches query vectors in a bounded TTL cache.

    Repeated queries are common (technical FAQs), so this avoids paying for the round-trip.
    LangChain's ``CacheBackedEmbeddings`` is not used because its in-memory store is unbounded.
    Document embeddings (ingestion) are delegated without caching.
    """

    def __init__(self, underlying: Embeddings, cache: TTLCache | None = None) -> None:
        self.underlying = underlying
        self._cache = cache
        self._lock = threading.Lock()  # TTLCache is not thread-safe (sync path may run in threads)

    def _get(self, key: str) -> list[float] | None:
        if self._cache is None:
            return None
        with self._lock:
            return self._cache.get(key)

    def _put(self, key: str, vector: list[float]) -> None:
        if self._cache is not None:
            with self._lock:
                self._cache[key] = vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.underlying.embed_documents(texts)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self.underlying.aembed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        key = text.strip()
        if (cached := self._get(key)) is not None:
            return cached
        vector = self.underlying.embed_query(key)
        self._put(key, vector)
        return vector

    async def aembed_query(self, text: str) -> list[float]:
        key = text.strip()
        if (cached := self._get(key)) is not None:
            return cached
        vector = await self.underlying.aembed_query(key)
        self._put(key, vector)
        return vector
