"""Offline, document-level retrieval metrics (no LLM).

Retrieved chunks are turned into a ranking of unique documents (first appearance of
each ``filepath``) and compared against the golden set's ``relevant_sources``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def ranked_documents(retrieved_sources: Sequence[str], k: int | None = None) -> list[str]:
    """Unique filepaths in order of first appearance within the top-``k`` chunks."""
    window = retrieved_sources if k is None else retrieved_sources[:k]
    return list(dict.fromkeys(window))


def hit_at_k(retrieved_sources: Sequence[str], relevant: Iterable[str], k: int) -> float:
    rel = set(relevant)
    return float(any(doc in rel for doc in ranked_documents(retrieved_sources, k)))


def recall_at_k(retrieved_sources: Sequence[str], relevant: Iterable[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    return len(rel & set(ranked_documents(retrieved_sources, k))) / len(rel)


def precision_at_k(retrieved_sources: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """Fraction of the top-``k`` chunks that come from a relevant document."""
    window = retrieved_sources[:k]
    if not window:
        return 0.0
    rel = set(relevant)
    return sum(src in rel for src in window) / len(window)


def reciprocal_rank(retrieved_sources: Sequence[str], relevant: Iterable[str]) -> float:
    rel = set(relevant)
    for rank, doc in enumerate(ranked_documents(retrieved_sources), start=1):
        if doc in rel:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved_sources: Sequence[str], relevant: Iterable[str], k: int) -> float:
    rel = set(relevant)
    if not rel:
        return 0.0
    docs = ranked_documents(retrieved_sources, k)
    dcg = sum(1.0 / math.log2(i + 2) for i, doc in enumerate(docs) if doc in rel)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / idcg if idcg else 0.0


def retrieval_report(retrieved_sources: Sequence[str], relevant: Sequence[str], top_n: int = 5) -> dict[str, float]:
    return {
        f"hit@{top_n}": hit_at_k(retrieved_sources, relevant, top_n),
        f"recall@{top_n}": recall_at_k(retrieved_sources, relevant, top_n),
        f"precision@{top_n}": precision_at_k(retrieved_sources, relevant, top_n),
        "recall@20": recall_at_k(retrieved_sources, relevant, 20),
        "mrr": reciprocal_rank(retrieved_sources, relevant),
        f"ndcg@{top_n}": ndcg_at_k(retrieved_sources, relevant, top_n),
    }


def mean_report(reports: Sequence[dict[str, float]]) -> dict[str, float]:
    if not reports:
        return {}
    return {key: round(sum(r[key] for r in reports) / len(reports), 4) for key in reports[0]}
