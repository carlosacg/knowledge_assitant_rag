"""Vector store: thin adapter over ``langchain_qdrant.QdrantVectorStore`` (cosine similarity).

The adapter keeps the rest of the system on the domain models (``Chunk`` / ``ScoredChunk``) and
adds what LangChain does not cover here: collection setup with payload indexes, metadata filters
and non-blocking calls. langchain-qdrant has no native async API, so Qdrant calls run in a thread
behind a ``CapacityLimiter``; the query embedding is computed with the async, cached
``aembed_query`` beforehand.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import partial

import anyio
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore as LCQdrantVectorStore
from qdrant_client import QdrantClient, models

from rag.models import Chunk, ScoredChunk, SearchFilters

logger = logging.getLogger(__name__)

# langchain-qdrant stores the metadata under this payload key.
METADATA_KEY = LCQdrantVectorStore.METADATA_KEY
KEYWORD_INDEXES = ("tech_stack", "environment", "doc_type", "filepath", "content_type")


def _field(name: str) -> str:
    return f"{METADATA_KEY}.{name}"


def build_qdrant_filter(filters: SearchFilters | None) -> models.Filter | None:
    if filters is None:
        return None
    must: list[models.Condition] = []
    if filters.tech_stack:
        must.append(
            models.FieldCondition(key=_field("tech_stack"), match=models.MatchAny(any=list(filters.tech_stack)))
        )
    if filters.environment:
        must.append(
            models.FieldCondition(
                key=_field("environment"), match=models.MatchAny(any=[filters.environment, "all"])
            )
        )
    if filters.doc_type:
        must.append(models.FieldCondition(key=_field("doc_type"), match=models.MatchValue(value=filters.doc_type)))
    return models.Filter(must=must) if must else None


class QdrantVectorStore:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        embeddings: Embeddings,
        dim: int,
        limiter: anyio.CapacityLimiter | None = None,
    ) -> None:
        self.client = client
        self.collection = collection
        self.embeddings = embeddings
        self.dim = dim
        self.limiter = limiter
        # validate_collection_config=False: the collection may not exist yet (API started before
        # ingestion), and validation would embed a dummy text (a network call) at startup.
        self.store = LCQdrantVectorStore(
            client=client,
            collection_name=collection,
            embedding=embeddings,
            distance=models.Distance.COSINE,
            validate_collection_config=False,
        )

    async def _run(self, func, *args, **kwargs):
        return await anyio.to_thread.run_sync(partial(func, *args, **kwargs), limiter=self.limiter)

    def _ensure_collection_sync(self, recreate: bool) -> None:
        exists = self.client.collection_exists(self.collection)
        if exists and recreate:
            self.client.delete_collection(self.collection)
            exists = False
        if exists:
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
        )
        for name in KEYWORD_INDEXES:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name=_field(name),
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        logger.info("colección creada", extra={"collection": self.collection, "dim": self.dim})

    async def ensure_collection(self, recreate: bool = False) -> None:
        await self._run(self._ensure_collection_sync, recreate)

    async def add_chunks(self, chunks: Sequence[Chunk], batch_size: int = 64) -> None:
        """Embeds (via LangChain) and upserts the chunks; ids are deterministic, so re-runs are idempotent."""
        await self.store.aadd_documents(
            [c.to_document() for c in chunks], ids=[c.point_id for c in chunks], batch_size=batch_size
        )

    async def search(self, query: str, top_k: int, filters: SearchFilters | None = None) -> list[ScoredChunk]:
        vector = await self.embeddings.aembed_query(query)
        results = await self._run(
            self.store.similarity_search_with_score_by_vector,
            vector,
            k=top_k,
            filter=build_qdrant_filter(filters),
        )
        return [
            ScoredChunk(Chunk.from_document(doc), float(score), {"dense": float(score)})
            for doc, score in results
        ]

    def _count_sync(self) -> int:
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection, exact=True).count

    async def count(self) -> int:
        return await self._run(self._count_sync)
