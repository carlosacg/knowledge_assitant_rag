"""Vector store client (Qdrant) for indexing and cosine similarity search."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models

from rag.models import Chunk, ScoredChunk, SearchFilters

logger = logging.getLogger(__name__)

KEYWORD_INDEXES = ("tech_stack", "environment", "doc_type", "filepath", "content_type")


def build_qdrant_filter(filters: SearchFilters | None) -> models.Filter | None:
    if filters is None:
        return None
    must: list[models.Condition] = []
    if filters.tech_stack:
        must.append(models.FieldCondition(key="tech_stack", match=models.MatchAny(any=list(filters.tech_stack))))
    if filters.environment:
        must.append(
            models.FieldCondition(key="environment", match=models.MatchAny(any=[filters.environment, "all"]))
        )
    if filters.doc_type:
        must.append(models.FieldCondition(key="doc_type", match=models.MatchValue(value=filters.doc_type)))
    return models.Filter(must=must) if must else None


class QdrantVectorStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, dim: int) -> None:
        self.client = client
        self.collection = collection
        self.dim = dim

    async def ensure_collection(self, recreate: bool = False) -> None:
        exists = await self.client.collection_exists(self.collection)
        if exists and recreate:
            await self.client.delete_collection(self.collection)
            exists = False
        if exists:
            return
        await self.client.create_collection(
            collection_name=self.collection,
            vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE),
        )
        for field in KEYWORD_INDEXES:
            await self.client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        logger.info("colección creada", extra={"collection": self.collection, "dim": self.dim})

    async def upsert(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]], batch_size: int = 128) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks y vectors deben tener el mismo largo")
        points = [
            models.PointStruct(id=chunk.point_id, vector=list(vector), payload=chunk.to_payload())
            for chunk, vector in zip(chunks, vectors)
        ]
        for i in range(0, len(points), batch_size):
            await self.client.upsert(collection_name=self.collection, points=points[i : i + batch_size], wait=True)

    async def search(
        self, vector: Sequence[float], top_k: int, filters: SearchFilters | None = None
    ) -> list[ScoredChunk]:
        response = await self.client.query_points(
            collection_name=self.collection,
            query=list(vector),
            query_filter=build_qdrant_filter(filters),
            limit=top_k,
            with_payload=True,
        )
        return [
            ScoredChunk(Chunk.from_payload(point.payload or {}), float(point.score), {"dense": float(point.score)})
            for point in response.points
        ]

    async def count(self) -> int:
        if not await self.client.collection_exists(self.collection):
            return 0
        return (await self.client.count(self.collection, exact=True)).count
