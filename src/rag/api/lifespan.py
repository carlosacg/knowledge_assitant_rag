"""Ciclo de vida de la aplicación: inicializa y libera recursos una vez por worker."""

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
    """Startup: clientes async con pool (OpenAI/Qdrant), BM25, reranker precalentado y limitadores.

    Todo queda en ``app.state.resources`` y se comparte entre requests del worker; ``depends.py``
    lo expone a los endpoints. Shutdown: cierre ordenado de conexiones (AsyncExitStack).
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
