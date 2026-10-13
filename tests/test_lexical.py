from __future__ import annotations

from rag.models import SearchFilters
from rag.retrieval.lexical import BM25Index, tokenize


def test_tokenizer_preserves_identifiers_and_subparts():
    tokens = tokenize("Configurar PAYMENTS_DB_URL y POST /api/v1/payments/refunds (evento payments.refund.requested).")
    for expected in ("payments_db_url", "payments", "db", "url", "api/v1/payments/refunds", "refunds",
                     "payments.refund.requested", "refund", "requested"):
        assert expected in tokens
    assert "y" not in tokens  # stopword


def test_bm25_finds_exact_identifier(sample_chunks):
    index = BM25Index.build(sample_chunks)
    results = index.search("PAYMENTS_DB_POOL_SIZE", top_k=5)
    assert results
    assert "PAYMENTS_DB_POOL_SIZE" in results[0].chunk.text


def test_bm25_filters(sample_chunks):
    index = BM25Index.build(sample_chunks)
    results = index.search("PAYMENTS_DB_URL", top_k=5, filters=SearchFilters.build(environment="staging"))
    assert results
    assert all(r.chunk.metadata["environment"] in ("staging", "all") for r in results)


def test_bm25_roundtrip(tmp_path, sample_chunks):
    path = tmp_path / "bm25.pkl"
    BM25Index.build(sample_chunks).save(path)
    loaded = BM25Index.load(path)
    assert len(loaded) == len(sample_chunks)
    assert loaded.search("select_related", 3)[0].chunk.metadata["filepath"] == "docs/backend/django.md"


def test_bm25_no_match_returns_empty(sample_chunks):
    assert BM25Index.build(sample_chunks).search("kubernetes azure", 5) == []
