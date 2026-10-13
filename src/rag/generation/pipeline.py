"""Pipeline RAG completo: hybrid search -> reranking -> contexto -> LLM -> citas."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import anyio

from rag.config import Settings
from rag.generation.context_builder import build_context, extract_citations
from rag.generation.llm import LLMClient
from rag.generation.prompts import ABSTENTION_MESSAGE, SYSTEM_PROMPT, build_user_prompt, is_abstention
from rag.models import Answer, ScoredChunk, SearchFilters
from rag.retrieval.hybrid import HybridRetriever, trace_ranking
from rag.retrieval.reranker import Reranker

logger = logging.getLogger(__name__)


class PipelineTimeoutError(Exception):
    """Una etapa del pipeline excedió su presupuesto de tiempo."""

    def __init__(self, stage: str, timeout_s: float) -> None:
        super().__init__(f"La etapa '{stage}' excedió {timeout_s}s")
        self.stage = stage


@dataclass
class RetrievalOutcome:
    chunks: list[ScoredChunk]
    reranked: bool
    debug: dict[str, Any] = field(default_factory=dict)


class RAGPipeline:
    def __init__(
        self,
        retriever: HybridRetriever,
        llm: LLMClient,
        settings: Settings,
        reranker: Reranker | None = None,
        rerank_limiter: anyio.CapacityLimiter | None = None,
    ) -> None:
        self.retriever = retriever
        self.llm = llm
        self.settings = settings
        self.reranker = reranker
        self.rerank_limiter = rerank_limiter

    # ------------------------------------------------------------------ retrieval

    async def _rerank(self, question: str, candidates: list[ScoredChunk]) -> list[ScoredChunk]:
        texts = [c.chunk.embed_text for c in candidates]
        # abandon_on_cancel: ante timeout, el request no espera al thread (que termina en segundo plano).
        scores = await anyio.to_thread.run_sync(
            self.reranker.score, question, texts, limiter=self.rerank_limiter, abandon_on_cancel=True
        )
        reranked = [
            ScoredChunk(c.chunk, score, {**c.scores, "rerank": score}) for c, score in zip(candidates, scores)
        ]
        return sorted(reranked, key=lambda c: c.score, reverse=True)

    async def retrieve(
        self,
        question: str,
        mode: str = "hybrid_rerank",
        filters: SearchFilters | None = None,
        top_n: int | None = None,
        keep_all: bool = False,
    ) -> RetrievalOutcome:
        """Top-K candidatos (hybrid/dense/bm25) y, en ``hybrid_rerank``, reordenados por el cross-encoder."""
        s = self.settings
        base_mode = "hybrid" if mode == "hybrid_rerank" else mode
        try:
            async with asyncio.timeout(s.retrieval_timeout_s):
                result = await self.retriever.retrieve(question, base_mode, s.top_k, filters)
        except TimeoutError as exc:
            raise PipelineTimeoutError("retrieval", s.retrieval_timeout_s) from exc

        candidates, debug, reranked = result.candidates, result.debug, False
        if mode == "hybrid_rerank" and candidates:
            if self.reranker is None:
                debug["rerank"] = "deshabilitado"
            else:
                try:
                    async with asyncio.timeout(s.rerank_timeout_s):
                        candidates = await self._rerank(question, candidates)
                    reranked = True
                    debug["rerank"] = trace_ranking(candidates)
                except TimeoutError:
                    # Degradación controlada: se usa el orden RRF en lugar de fallar el request.
                    debug["rerank_degraded"] = f"timeout > {s.rerank_timeout_s}s, se usa orden RRF"
                    logger.warning("reranker timeout; degradando a RRF")

        if not keep_all:
            candidates = candidates[: top_n or s.top_n]
        return RetrievalOutcome(candidates, reranked, debug)

    # ------------------------------------------------------------------ generation

    async def ask(
        self,
        question: str,
        mode: str = "hybrid_rerank",
        filters: SearchFilters | None = None,
    ) -> Answer:
        s = self.settings
        started = time.perf_counter()
        outcome = await self.retrieve(question, mode, filters)
        debug: dict[str, Any] = {"retrieval": outcome.debug, "reranked": outcome.reranked}
        timings = {"retrieval_ms": _ms(started)}

        def abstain(reason: str) -> Answer:
            debug.update(abstention_reason=reason, timings=timings)
            return Answer(question, ABSTENTION_MESSAGE, [], outcome.chunks, True, mode, debug)

        if not outcome.chunks:
            return abstain("sin_resultados")
        # Umbral de relevancia: solo es fiable con scores calibrados del cross-encoder.
        if outcome.reranked and outcome.chunks[0].score < s.min_rerank_score:
            return abstain(f"relevancia_baja (top={outcome.chunks[0].score:.4f} < {s.min_rerank_score})")

        context = build_context(outcome.chunks, s.context_max_tokens)
        debug["context_tokens"] = context.tokens
        llm_started = time.perf_counter()
        try:
            async with asyncio.timeout(s.llm_timeout_s):
                text = await self.llm.complete(SYSTEM_PROMPT, build_user_prompt(question, context.text))
        except TimeoutError as exc:
            raise PipelineTimeoutError("llm", s.llm_timeout_s) from exc
        timings.update(llm_ms=_ms(llm_started), total_ms=_ms(started))
        debug["timings"] = timings

        abstained = is_abstention(text) and not extract_citations(text, context.used)
        citations = [] if abstained else extract_citations(text, context.used)
        return Answer(question, text, citations, context.used, abstained, mode, debug)


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)
