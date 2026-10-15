from __future__ import annotations

import pytest

from rag.models import Chunk, ScoredChunk, SearchFilters
from rag.retrieval.hybrid import reciprocal_rank_fusion
from rag.retrieval.vector_store import build_qdrant_filter


def _sc(cid: str, score: float = 1.0) -> ScoredChunk:
    return ScoredChunk(Chunk(cid, cid, cid, {"filepath": f"{cid}.md"}), score)


def test_rrf_rewards_agreement_between_rankers():
    dense = [_sc("a"), _sc("b"), _sc("c")]
    bm25 = [_sc("d"), _sc("b"), _sc("c")]
    fused = reciprocal_rank_fusion({"dense": dense, "bm25": bm25}, k=60)
    ids = [f.chunk_id for f in fused]
    assert ids[0] == "b"  # rank 2 in both (2/62) > rank 1 in only one (1/61)
    assert set(ids) == {"a", "b", "c", "d"}
    assert fused[0].score == pytest.approx(2 / 62)
    assert fused[0].scores["dense_rank"] == 2 and fused[0].scores["bm25_rank"] == 2


def test_rrf_top_k():
    assert len(reciprocal_rank_fusion({"x": [_sc(str(i)) for i in range(10)]}, top_k=3)) == 3


def test_qdrant_filter_includes_environment_all():
    flt = build_qdrant_filter(SearchFilters.build(["AWS"], "staging", None))
    conditions = {c.key: c.match for c in flt.must}
    # langchain-qdrant nests the metadata under the "metadata" payload key.
    assert conditions["metadata.tech_stack"].any == ["aws"]
    assert set(conditions["metadata.environment"].any) == {"staging", "all"}
    assert build_qdrant_filter(None) is None


def test_chunk_document_roundtrip():
    chunk = Chunk("abc", "texto", "Doc > Sec\n\ntexto", {"filepath": "docs/x.md", "tech_stack": ["aws"]})
    doc = chunk.to_document()
    assert doc.page_content == chunk.embed_text  # what LangChain embeds
    assert doc.id == chunk.point_id
    # langchain-qdrant adds private keys on reads; they must not leak into the domain metadata.
    doc.metadata.update({"_id": chunk.point_id, "_collection_name": "c"})
    assert Chunk.from_document(doc) == chunk


async def test_hybrid_retrieves_exact_identifier(retriever):
    result = await retriever.retrieve("¿Cuánto vale PAYMENTS_DB_POOL_SIZE?", "hybrid", top_k=5)
    assert result.candidates
    assert "PAYMENTS_DB_POOL_SIZE" in result.candidates[0].chunk.text
    assert {"dense", "bm25", "rrf"} <= set(result.debug)


@pytest.mark.parametrize("mode", ["dense", "bm25", "hybrid"])
async def test_all_modes_respect_filters(retriever, mode):
    filters = SearchFilters.build(environment="staging")
    result = await retriever.retrieve("PAYMENTS_DB_URL variables de entorno", mode, top_k=10, filters=filters)
    assert result.candidates
    assert all(c.chunk.metadata["environment"] in ("staging", "all") for c in result.candidates)


async def test_dense_search_semantic(retriever):
    result = await retriever.retrieve("consultas N+1 select_related prefetch_related", "dense", top_k=3)
    assert result.candidates[0].chunk.metadata["filepath"] == "docs/backend/django.md"


async def test_unknown_mode_raises(retriever):
    with pytest.raises(ValueError):
        await retriever.retrieve("x", "magic", top_k=3)
