"""Fakes deterministas (sin red) para embeddings, LLM, reranker y un corpus de ejemplo."""

from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
from typing import Any

import pytest
from qdrant_client import AsyncQdrantClient

from rag.config import Settings
from rag.ingestion.chunker import MarkdownChunker
from rag.models import Chunk, Document
from rag.retrieval.hybrid import HybridRetriever
from rag.retrieval.lexical import BM25Index, tokenize
from rag.retrieval.vector_store import QdrantVectorStore

FAKE_DIM = 64


def word_count(text: str) -> int:
    return len(text.split())


class FakeEmbedder:
    """Bag-of-words hasheado y normalizado: textos con vocabulario común quedan cercanos."""

    dim = FAKE_DIM

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * FAKE_DIM
        for token in tokenize(text):
            vec[int(hashlib.md5(token.encode()).hexdigest(), 16) % FAKE_DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeReranker:
    """Score = proporción de tokens de la consulta presentes en el texto (0..1)."""

    loaded = True

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = set(tokenize(query))
        return [len(q & set(tokenize(t))) / max(1, len(q)) for t in texts]


class SlowReranker(FakeReranker):
    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    def score(self, query: str, texts: list[str]) -> list[float]:
        import time

        time.sleep(self.delay_s)
        return super().score(query, texts)


class FakeLLM:
    def __init__(self, response: str = "La variable se obtiene de Secrets Manager [1].", delay_s: float = 0.0):
        self.response = response
        self.delay_s = delay_s
        self.calls: list[dict[str, str]] = []

    async def complete(self, system: str, user: str) -> str:
        self.calls.append({"system": system, "user": user})
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.response


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "openai_api_key": "sk-test",
        "qdrant_url": ":memory:",
        "data_dir": tmp_path,
        "docs_dir": tmp_path / "docs",
        "embedding_dim": FAKE_DIM,
        "reranker_enabled": False,
        "top_k": 10,
        "top_n": 3,
        "min_rerank_score": 0.2,
    }
    base.update(overrides)
    return Settings(**base)


SAMPLE_DOCS = [
    Document(
        filepath="docs/infra/aws-production.md",
        content="""# Despliegue AWS

## Variables de entorno

El servicio payments-service lee PAYMENTS_DB_URL desde AWS Secrets Manager en la ruta prod/payments/db-url.
El pool de conexiones se define con PAYMENTS_DB_POOL_SIZE y en producción vale 20.

## Autoescalado

Las tareas ECS escalan entre 4 y 20 réplicas con un objetivo de CPU del 60 por ciento.
""",
        metadata={
            "title": "Despliegue AWS",
            "filepath": "docs/infra/aws-production.md",
            "tech_stack": ["aws", "fastapi"],
            "doc_type": "deployment",
            "environment": "production",
            "last_updated": "2026-08-01",
        },
    ),
    Document(
        filepath="docs/infra/aws-staging.md",
        content="""# Staging AWS

## Variables de entorno

En staging PAYMENTS_DB_URL proviene de staging/payments/db-url y PAYMENTS_DB_POOL_SIZE vale 5.
""",
        metadata={
            "title": "Staging AWS",
            "filepath": "docs/infra/aws-staging.md",
            "tech_stack": ["aws"],
            "doc_type": "deployment",
            "environment": "staging",
            "last_updated": "2026-07-01",
        },
    ),
    Document(
        filepath="docs/backend/django.md",
        content="""# Estándares Django

## Settings

La configuración se divide en config/settings/base.py y production.py y se selecciona con DJANGO_SETTINGS_MODULE.

## ORM

Es obligatorio usar select_related y prefetch_related para evitar consultas N+1.
""",
        metadata={
            "title": "Estándares Django",
            "filepath": "docs/backend/django.md",
            "tech_stack": ["django", "python"],
            "doc_type": "standard",
            "environment": "all",
            "last_updated": "2026-05-10",
        },
    ),
]


@pytest.fixture
def sample_chunks() -> list[Chunk]:
    chunker = MarkdownChunker(chunk_size=60, chunk_overlap=10, max_code_tokens=80, counter=word_count)
    return [c for doc in SAMPLE_DOCS for c in chunker.chunk(doc)]


@pytest.fixture
async def retriever(sample_chunks: list[Chunk]) -> HybridRetriever:
    client = AsyncQdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "test_docs", FAKE_DIM)
    embedder = FakeEmbedder()
    await store.ensure_collection(recreate=True)
    await store.upsert(sample_chunks, await embedder.embed_documents([c.embed_text for c in sample_chunks]))
    yield HybridRetriever(embedder, store, BM25Index.build(sample_chunks))
    await client.close()
