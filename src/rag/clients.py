"""Fábricas de clientes externos (OpenAI, Qdrant) compartidas por CLI, API y evaluación."""

from __future__ import annotations

import httpx
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from rag.config import Settings


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Pool de conexiones compartido: evita abrir un socket TLS por request bajo alta concurrencia."""
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.http_max_connections,
            max_keepalive_connections=settings.http_max_keepalive,
        ),
        timeout=httpx.Timeout(settings.openai_timeout_s, connect=5.0),
    )


def create_openai_client(settings: Settings, http_client: httpx.AsyncClient | None = None) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=settings.require_openai_key(),
        max_retries=settings.openai_max_retries,
        timeout=settings.openai_timeout_s,
        http_client=http_client,
    )


def create_qdrant_client(settings: Settings) -> AsyncQdrantClient:
    if settings.qdrant_url == ":memory:":
        return AsyncQdrantClient(location=":memory:")
    api_key = settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None
    return AsyncQdrantClient(url=settings.qdrant_url, api_key=api_key, timeout=settings.qdrant_timeout_s)
