"""Full evaluation: retrieval metrics per mode + abstention + Ragas on the final pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from rag.config import Settings
from rag.evaluation.golden_set import GoldenItem, load_golden_set
from rag.evaluation.ragas_eval import RagasSample, run_ragas
from rag.evaluation.retrieval_metrics import mean_report, retrieval_report
from rag.generation.pipeline import RAGPipeline
from rag.models import Answer
from rag.resources import open_resources

logger = logging.getLogger(__name__)
T = TypeVar("T")


async def _bounded_gather(factories: Sequence[Callable[[], Awaitable[T]]], concurrency: int) -> list[T]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run(factory: Callable[[], Awaitable[T]]) -> T:
        async with semaphore:
            return await factory()

    return await asyncio.gather(*(run(f) for f in factories))


async def evaluate_retrieval(
    pipeline: RAGPipeline, items: Sequence[GoldenItem], modes: Sequence[str], top_n: int, concurrency: int = 4
) -> dict[str, Any]:
    answerable = [i for i in items if i.answerable]
    results: dict[str, Any] = {}
    for mode in modes:

        async def evaluate_item(item: GoldenItem, mode: str = mode) -> dict[str, Any]:
            # keep_all: the full ranking (top-K, reranked if applicable) to measure recall@20 and MRR.
            outcome = await pipeline.retrieve(item.question, mode, keep_all=True)
            sources = [c.chunk.metadata.get("filepath", "") for c in outcome.chunks]
            return {
                "id": item.id,
                "category": item.category,
                **retrieval_report(sources, item.relevant_sources, top_n),
                "top_sources": list(dict.fromkeys(sources))[:top_n],
            }

        per_question = await _bounded_gather([lambda i=i: evaluate_item(i) for i in answerable], concurrency)
        metric_keys = [k for k in per_question[0] if k not in ("id", "category", "top_sources")] if per_question else []
        summary = mean_report([{k: q[k] for k in metric_keys} for q in per_question])
        results[mode] = {"summary": summary, "per_question": per_question}
        logger.info("retrieval evaluado", extra={"mode": mode, **summary})
    return results


def abstention_metrics(records: Sequence[tuple[GoldenItem, Answer]]) -> dict[str, float]:
    unanswerable = [a for item, a in records if not item.answerable]
    answerable = [a for item, a in records if item.answerable]
    return {
        "abstention_accuracy": round(sum(a.abstained for a in unanswerable) / len(unanswerable), 4)
        if unanswerable
        else 0.0,
        "false_abstention_rate": round(sum(a.abstained for a in answerable) / len(answerable), 4)
        if answerable
        else 0.0,
        "citation_rate": round(sum(bool(a.citations) for a in answerable) / len(answerable), 4)
        if answerable
        else 0.0,
    }


async def collect(
    settings: Settings, items: Sequence[GoldenItem], modes: Sequence[str], concurrency: int
) -> tuple[dict[str, Any], list[tuple[GoldenItem, Answer]]]:
    async with open_resources(settings) as resources:
        pipeline = resources.pipeline
        retrieval = await evaluate_retrieval(pipeline, items, modes, settings.top_n, concurrency)
        answers = await _bounded_gather(
            [lambda i=i: pipeline.ask(i.question, "hybrid_rerank") for i in items], concurrency
        )
    return retrieval, list(zip(items, answers))


def run_evaluation(
    settings: Settings,
    modes: Sequence[str],
    *,
    with_ragas: bool = True,
    concurrency: int = 4,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    items = load_golden_set(settings.golden_set_path)
    retrieval, records = asyncio.run(collect(settings, items, modes, concurrency))

    results: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config": {
            "embedding_model": settings.embedding_model,
            "llm_model": settings.llm_model,
            "reranker_model": settings.reranker_model if settings.reranker_enabled else None,
            "top_k": settings.top_k,
            "top_n": settings.top_n,
            "questions": len(items),
        },
        "retrieval": retrieval,
        "generation": {
            "abstention": abstention_metrics(records),
            "answers": [
                {
                    "id": item.id,
                    "question": item.question,
                    "answer": answer.text,
                    "abstained": answer.abstained,
                    "citations": [c.filepath for c in answer.citations],
                }
                for item, answer in records
            ],
        },
    }

    if with_ragas:
        samples = [
            RagasSample(
                user_input=item.question,
                retrieved_contexts=[c.chunk.text for c in answer.contexts],
                response=answer.text,
                reference=item.ground_truth,
            )
            for item, answer in records
            if item.answerable
        ]
        summary, per_sample = run_ragas(samples, settings)
        results["ragas"] = {"summary": summary, "per_sample": per_sample}

    out_dir = output_dir or settings.eval_output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name in (f"results_{stamp}.json", "latest_results.json"):
        (out_dir / name).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    results["output_path"] = str(out_dir / f"results_{stamp}.json")
    return results
