from __future__ import annotations

from fastapi import APIRouter, Response, status

from rag.api.depends import ResourcesDep
from rag.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Liveness: el proceso responde (no toca dependencias externas)."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=HealthResponse)
async def readiness(resources: ResourcesDep, response: Response) -> HealthResponse:
    """Readiness: Qdrant accesible con datos indexados, índice BM25 cargado y reranker listo."""
    checks: dict[str, object] = {}
    try:
        checks["qdrant_points"] = await resources.vector_store.count()
    except Exception as exc:  # noqa: BLE001 - se reporta el motivo
        checks["qdrant_error"] = type(exc).__name__
    checks["bm25_chunks"] = len(resources.bm25) if resources.bm25 else 0
    checks["reranker_loaded"] = bool(resources.reranker and resources.reranker.loaded)
    checks["in_flight"] = resources.request_limiter.in_flight

    if not checks.get("qdrant_points"):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable", checks=checks)
    reranker_ok = checks["reranker_loaded"] or not resources.settings.reranker_enabled
    if not checks["bm25_chunks"] or not reranker_ok:
        return HealthResponse(status="degraded", checks=checks)
    return HealthResponse(status="ok", checks=checks)
