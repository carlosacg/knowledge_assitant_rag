"""Aplicación FastAPI (``rag.api.app:app`` para gunicorn/uvicorn)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from rag.api.lifespan import lifespan as default_lifespan
from rag.api.middleware import RequestContextMiddleware
from rag.api.routers import ask, health
from rag.generation.pipeline import PipelineTimeoutError

logger = logging.getLogger(__name__)

LifespanFactory = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def _error(request: Request, code: str, message: str, status_code: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def create_app(lifespan: LifespanFactory | None = default_lifespan) -> FastAPI:
    app = FastAPI(
        title="Engineering Knowledge RAG",
        version="0.1.0",
        description="Asistente técnico con respuestas respaldadas por la documentación interna.",
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health.router)
    app.include_router(ask.router)

    @app.exception_handler(PipelineTimeoutError)
    async def _timeout_handler(request: Request, exc: PipelineTimeoutError) -> JSONResponse:
        logger.warning("timeout de pipeline", extra={"stage": exc.stage})
        return _error(request, "PIPELINE_TIMEOUT", str(exc), status.HTTP_504_GATEWAY_TIMEOUT)

    @app.exception_handler(Exception)
    async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("error no controlado")
        return _error(request, "INTERNAL_ERROR", "Error interno", status.HTTP_500_INTERNAL_SERVER_ERROR)

    return app


app = create_app()
