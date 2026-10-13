from __future__ import annotations

import anyio
import pytest

from rag.generation.pipeline import PipelineTimeoutError, RAGPipeline
from rag.generation.prompts import ABSTENTION_MESSAGE
from rag.models import SearchFilters
from tests.conftest import FakeLLM, FakeReranker, SlowReranker, make_settings


def _pipeline(retriever, tmp_path, llm=None, reranker=None, **settings):
    return RAGPipeline(
        retriever,
        llm or FakeLLM(),
        make_settings(tmp_path, **settings),
        reranker=reranker or FakeReranker(),
        rerank_limiter=anyio.CapacityLimiter(2),
    )


async def test_answer_with_citations(retriever, tmp_path):
    llm = FakeLLM("En producción PAYMENTS_DB_URL sale de prod/payments/db-url [1].")
    pipeline = _pipeline(retriever, tmp_path, llm=llm)
    answer = await pipeline.ask("¿De dónde sale PAYMENTS_DB_URL en producción?")
    assert not answer.abstained
    assert answer.citations and answer.citations[0].index == 1
    assert answer.debug["reranked"] is True
    assert "rerank" in answer.debug["retrieval"]
    # The context sent to the LLM includes visible metadata.
    assert "[1] Fuente: docs/" in llm.calls[0]["user"]
    assert len(answer.contexts) <= 3


async def test_low_relevance_abstains_without_calling_llm(retriever, tmp_path):
    llm = FakeLLM()
    pipeline = _pipeline(retriever, tmp_path, llm=llm, min_rerank_score=0.5)
    answer = await pipeline.ask("¿Qué versión de Kubernetes usa Azure AKS?")
    assert answer.abstained
    assert answer.text == ABSTENTION_MESSAGE
    assert llm.calls == []
    assert "abstention_reason" in answer.debug


async def test_llm_abstention_detected(retriever, tmp_path):
    pipeline = _pipeline(retriever, tmp_path, llm=FakeLLM(ABSTENTION_MESSAGE))
    answer = await pipeline.ask("¿Cuánto vale PAYMENTS_DB_POOL_SIZE?")
    assert answer.abstained and answer.citations == []


async def test_rerank_timeout_degrades_to_rrf(retriever, tmp_path):
    pipeline = _pipeline(retriever, tmp_path, reranker=SlowReranker(0.5), rerank_timeout_s=0.05)
    answer = await pipeline.ask("¿Cuánto vale PAYMENTS_DB_POOL_SIZE?")
    assert "rerank_degraded" in answer.debug["retrieval"]
    assert answer.debug["reranked"] is False
    assert not answer.abstained  # without rerank the threshold is not applied


async def test_llm_timeout_raises(retriever, tmp_path):
    pipeline = _pipeline(retriever, tmp_path, llm=FakeLLM(delay_s=0.5), llm_timeout_s=0.05)
    with pytest.raises(PipelineTimeoutError) as exc:
        await pipeline.ask("¿Cuánto vale PAYMENTS_DB_POOL_SIZE?")
    assert exc.value.stage == "llm"


async def test_retrieve_keep_all_and_filters(retriever, tmp_path):
    pipeline = _pipeline(retriever, tmp_path)
    outcome = await pipeline.retrieve(
        "PAYMENTS_DB_URL", "hybrid_rerank", SearchFilters.build(environment="staging"), keep_all=True
    )
    assert outcome.reranked
    assert all(c.chunk.metadata["environment"] in ("staging", "all") for c in outcome.chunks)
    scores = [c.score for c in outcome.chunks]
    assert scores == sorted(scores, reverse=True)
