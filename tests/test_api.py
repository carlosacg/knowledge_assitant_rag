from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from rag.api.app import create_app
from rag.api.depends import get_resources
from rag.config import get_settings
from rag.generation.pipeline import PipelineTimeoutError
from rag.models import Answer, Citation
from rag.resources import InFlightLimiter
from tests.conftest import make_settings


class FakePipeline:
    def __init__(self, delay_s: float = 0.0, raise_timeout: bool = False) -> None:
        self.delay_s = delay_s
        self.raise_timeout = raise_timeout
        self.calls: list[dict[str, Any]] = []

    async def ask(self, question, mode="hybrid_rerank", filters=None) -> Answer:
        self.calls.append({"question": question, "mode": mode, "filters": filters})
        if self.raise_timeout:
            raise PipelineTimeoutError("llm", 1.0)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        citation = Citation(1, "docs/infra/aws-production-deployment.md", "Variables", "AWS", 0.91)
        return Answer(question, "Viene de Secrets Manager [1].", [citation], [], False, mode, {"reranked": True})


class FakeVectorStore:
    def __init__(self, points: int = 100) -> None:
        self.points = points

    async def count(self) -> int:
        return self.points


@dataclass
class FakeResources:
    pipeline: FakePipeline
    settings: Any
    vector_store: FakeVectorStore = field(default_factory=FakeVectorStore)
    bm25: Any = None
    reranker: Any = None
    request_limiter: InFlightLimiter = field(default_factory=lambda: InFlightLimiter(64))


def build_client(resources: FakeResources) -> httpx.AsyncClient:
    app = create_app(lifespan=None)
    app.dependency_overrides[get_resources] = lambda: resources
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def resources(tmp_path) -> FakeResources:
    return FakeResources(pipeline=FakePipeline(), settings=make_settings(tmp_path))


async def test_ask_returns_answer_with_citations(resources):
    async with build_client(resources) as client:
        resp = await client.post(
            "/ask",
            json={"question": "¿De dónde sale PAYMENTS_DB_URL?", "filters": {"environment": "production"}},
            headers={"X-Request-ID": "req-123"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"][0]["filepath"] == "docs/infra/aws-production-deployment.md"
    assert body["request_id"] == "req-123"
    assert body["debug"] is None
    assert resp.headers["x-request-id"] == "req-123"
    assert "x-process-time-ms" in resp.headers
    assert resources.pipeline.calls[0]["filters"].environment == "production"


async def test_ask_validation_error(resources):
    async with build_client(resources) as client:
        resp = await client.post("/ask", json={"question": "x", "mode": "magic"})
    assert resp.status_code == 422


async def test_ask_debug_flag(resources):
    async with build_client(resources) as client:
        resp = await client.post("/ask", json={"question": "¿pool size?", "debug": True})
    assert resp.json()["debug"] == {"reranked": True}


async def test_pipeline_timeout_maps_to_504(resources):
    resources.pipeline = FakePipeline(raise_timeout=True)
    async with build_client(resources) as client:
        resp = await client.post("/ask", json={"question": "¿pool size?"})
    assert resp.status_code == 504
    assert resp.json()["error"]["code"] == "PIPELINE_TIMEOUT"


async def test_health_and_readiness(resources):
    async with build_client(resources) as client:
        assert (await client.get("/health")).json() == {"status": "ok", "checks": {}}
        ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded"  # no BM25 loaded
    resources.vector_store.points = 0
    async with build_client(resources) as client:
        assert (await client.get("/health/ready")).status_code == 503


async def test_lifespan_initializes_and_releases_resources(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        app = create_app()
        async with app.router.lifespan_context(app):
            res = app.state.resources
            assert res.pipeline is not None
            assert res.bm25 is None  # no ingestion yet
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
                ready = await client.get("/health/ready")
            assert ready.status_code == 503
            assert ready.json()["checks"]["qdrant_points"] == 0
    finally:
        get_settings.cache_clear()
