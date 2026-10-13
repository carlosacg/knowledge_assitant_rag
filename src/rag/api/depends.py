"""Dependencias de FastAPI.

Ninguna dependencia crea objetos por request: todas leen recursos ya inicializados en el
``lifespan`` (``app.state.resources``). Para tests basta con ``app.dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from rag.config import Settings
from rag.config import get_settings as _get_settings
from rag.generation.pipeline import RAGPipeline
from rag.resources import AppResources
from rag.retrieval.vector_store import QdrantVectorStore


def get_settings() -> Settings:
    return _get_settings()


def get_resources(request: Request) -> AppResources:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Servicio inicializándose")
    return resources


ResourcesDep = Annotated[AppResources, Depends(get_resources)]


def get_pipeline(resources: ResourcesDep) -> RAGPipeline:
    return resources.pipeline


def get_vector_store(resources: ResourcesDep) -> QdrantVectorStore:
    return resources.vector_store


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


async def concurrency_guard(resources: ResourcesDep) -> AsyncIterator[None]:
    """Backpressure: si el worker ya tiene el máximo de requests en vuelo, rechaza con 503.

    Rechazar rápido es preferible a encolar sin límite: mantiene la latencia acotada y le
    permite al balanceador / cliente reintentar en otro worker.
    """
    limiter = resources.request_limiter
    if not limiter.try_acquire():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servidor saturado, reintente en unos segundos.",
            headers={"Retry-After": "1"},
        )
    try:
        yield
    finally:
        limiter.release()


PipelineDep = Annotated[RAGPipeline, Depends(get_pipeline)]
VectorStoreDep = Annotated[QdrantVectorStore, Depends(get_vector_store)]
RequestIdDep = Annotated[str, Depends(get_request_id)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
