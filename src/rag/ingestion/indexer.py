"""Orquestación de la ingesta: load -> chunk -> embed -> upsert (Qdrant) + índice BM25."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rag.config import Settings
from rag.ingestion.chunker import MarkdownChunker
from rag.ingestion.loader import load_documents
from rag.models import Chunk
from rag.retrieval.embeddings import Embedder
from rag.retrieval.lexical import BM25Index
from rag.retrieval.vector_store import QdrantVectorStore

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    documents: int
    chunks: int
    by_content_type: dict[str, int] = field(default_factory=dict)
    by_file: dict[str, int] = field(default_factory=dict)
    bm25_path: str = ""


def chunk_corpus(settings: Settings, docs_dir: Path | None = None) -> tuple[int, list[Chunk]]:
    documents = load_documents(docs_dir or settings.docs_dir)
    chunker = MarkdownChunker(
        chunk_size=settings.chunk_size_tokens,
        chunk_overlap=settings.chunk_overlap_tokens,
        max_code_tokens=settings.max_code_tokens,
        intro_max_tokens=settings.code_intro_max_tokens,
    )
    return len(documents), [chunk for doc in documents for chunk in chunker.chunk(doc)]


async def run_ingestion(
    settings: Settings,
    embedder: Embedder,
    vector_store: QdrantVectorStore,
    *,
    rebuild: bool = True,
) -> IngestStats:
    n_docs, chunks = chunk_corpus(settings)
    if not chunks:
        raise RuntimeError(f"No se generaron chunks a partir de {settings.docs_dir}")

    vectors = await embedder.embed_documents([c.embed_text for c in chunks])
    await vector_store.ensure_collection(recreate=rebuild)
    await vector_store.upsert(chunks, vectors)

    BM25Index.build(chunks).save(settings.bm25_path)

    stats = IngestStats(
        documents=n_docs,
        chunks=len(chunks),
        by_content_type=dict(Counter(c.metadata["content_type"] for c in chunks)),
        by_file=dict(Counter(c.metadata["filepath"] for c in chunks)),
        bm25_path=str(settings.bm25_path),
    )
    logger.info("ingesta completada", extra={"documents": n_docs, "chunks": len(chunks)})
    return stats
