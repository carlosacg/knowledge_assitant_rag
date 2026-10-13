from __future__ import annotations

import asyncio
import time

from rag.resources import InFlightLimiter
from tests.conftest import make_settings
from tests.test_api import FakePipeline, FakeResources, build_client


async def test_requests_are_processed_concurrently(tmp_path):
    resources = FakeResources(pipeline=FakePipeline(delay_s=0.2), settings=make_settings(tmp_path))
    async with build_client(resources) as client:
        started = time.perf_counter()
        responses = await asyncio.gather(
            *(client.post("/ask", json={"question": f"pregunta {i}"}) for i in range(20))
        )
        elapsed = time.perf_counter() - started
    assert all(r.status_code == 200 for r in responses)
    # Secuencialmente serían ~4 s; en paralelo, ~0.2 s (el event loop nunca se bloquea).
    assert elapsed < 1.5
    assert resources.request_limiter.in_flight == 0


async def test_backpressure_returns_503_when_saturated(tmp_path):
    resources = FakeResources(
        pipeline=FakePipeline(delay_s=0.3),
        settings=make_settings(tmp_path),
        request_limiter=InFlightLimiter(2),
    )
    async with build_client(resources) as client:
        responses = await asyncio.gather(*(client.post("/ask", json={"question": f"q {i}"}) for i in range(5)))
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 200, 503, 503, 503]
    rejected = next(r for r in responses if r.status_code == 503)
    assert rejected.headers["retry-after"] == "1"
    assert resources.request_limiter.in_flight == 0  # todos los slots se liberaron


def test_inflight_limiter():
    limiter = InFlightLimiter(1)
    assert limiter.try_acquire()
    assert not limiter.try_acquire()
    limiter.release()
    assert limiter.try_acquire()
