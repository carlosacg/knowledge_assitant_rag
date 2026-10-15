"""External client factories (LangChain OpenAI models, Qdrant) shared by the CLI, API and evaluation."""

from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from qdrant_client import QdrantClient

from rag.config import Settings


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Shared connection pool: avoids opening a TLS socket per request under high concurrency."""
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive,
        ),
        timeout=httpx.Timeout(settings.openai_timeout_s, connect=5.0),
    )


def create_embeddings(settings: Settings, http_client: httpx.AsyncClient | None = None) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.require_openai_key(),
        chunk_size=settings.embedding_batch_size,  # texts per embeddings request
        max_retries=settings.openai_max_retries,
        request_timeout=settings.openai_timeout_s,
        http_async_client=http_client,
    )


def create_chat_model(settings: Settings, http_client: httpx.AsyncClient | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.require_openai_key(),
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=settings.openai_max_retries,
        request_timeout=settings.openai_timeout_s,
        http_async_client=http_client,
    )


def create_qdrant_client(settings: Settings) -> QdrantClient:
    """Sync client: langchain-qdrant has no async API (calls run in threads, see vector_store.py)."""
    if settings.qdrant_url == ":memory:":
        return QdrantClient(location=":memory:")
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return QdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=settings.qdrant_timeout_s)
