from __future__ import annotations

import math

import pytest

from rag.evaluation.retrieval_metrics import (
    hit_at_k,
    mean_report,
    ndcg_at_k,
    precision_at_k,
    ranked_documents,
    recall_at_k,
    reciprocal_rank,
)

RETRIEVED = ["a.md", "a.md", "b.md", "c.md", "d.md", "e.md"]


def test_ranked_documents_dedup():
    assert ranked_documents(RETRIEVED, 3) == ["a.md", "b.md"]


def test_hit_and_recall():
    assert hit_at_k(RETRIEVED, ["c.md"], 3) == 0.0
    assert hit_at_k(RETRIEVED, ["c.md"], 4) == 1.0
    assert recall_at_k(RETRIEVED, ["b.md", "z.md"], 5) == 0.5
    assert recall_at_k(RETRIEVED, [], 5) == 0.0


def test_precision_counts_chunks():
    assert precision_at_k(RETRIEVED, ["a.md"], 4) == 0.5


def test_mrr():
    assert reciprocal_rank(RETRIEVED, ["c.md"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(RETRIEVED, ["z.md"]) == 0.0


def test_ndcg():
    assert ndcg_at_k(RETRIEVED, ["a.md"], 5) == 1.0
    expected = (1 / math.log2(3)) / (1 / math.log2(2) + 1 / math.log2(3))
    assert ndcg_at_k(RETRIEVED, ["b.md", "z.md"], 5) == pytest.approx(expected)


def test_mean_report():
    assert mean_report([{"m": 1.0}, {"m": 0.0}]) == {"m": 0.5}
    assert mean_report([]) == {}
