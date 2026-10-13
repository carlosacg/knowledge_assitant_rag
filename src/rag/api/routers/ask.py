from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from rag.api.depends import PipelineDep, RequestIdDep, concurrency_guard
from rag.api.schemas import AskRequest, AskResponse, ErrorResponse

router = APIRouter(tags=["rag"])


@router.post(
    "/ask",
    response_model=AskResponse,
    dependencies=[Depends(concurrency_guard)],
    responses={503: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
async def ask(body: AskRequest, pipeline: PipelineDep, request_id: RequestIdDep) -> AskResponse:
    started = time.perf_counter()
    filters = body.filters.to_domain() if body.filters else None
    answer = await pipeline.ask(body.question, mode=body.mode, filters=filters)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return AskResponse.from_answer(answer, latency_ms=latency_ms, request_id=request_id, debug=body.debug)
