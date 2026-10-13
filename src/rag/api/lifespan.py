"""Application lifecycle: initializes and releases resources once per worker."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from rag.config import get_settings
from rag.log import configure_logging
from rag.resources import open_resources

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: pooled async clients (OpenAI/Qdrant), BM25, pre-warmed reranker and limiters.

    Everything lives in ``app.state.resources`` and is shared across the worker's requests;
    ``depends.py`` exposes it to the endpoints. Shutdown: orderly connection close (AsyncExitStack).
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    async with open_resources(settings) as resources:
        app.state.resources = resources
        logger.info("api lista", extra={"max_in_flight": settings.max_in_flight_requests})
        try:
            yield
        finally:
            logger.info("api apagándose")
