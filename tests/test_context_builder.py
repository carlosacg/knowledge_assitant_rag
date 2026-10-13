from __future__ import annotations

from rag.generation.context_builder import build_context, extract_citations, format_header
from rag.generation.prompts import ABSTENTION_MESSAGE, SYSTEM_PROMPT, build_user_prompt, is_abstention
from rag.models import Chunk, ScoredChunk


def _sc(cid: str, text: str, score: float = 0.9, **meta) -> ScoredChunk:
    metadata = {
        "filepath": f"docs/{cid}.md",
        "section": "Variables",
        "title": cid.upper(),
        "tech_stack": ["aws", "fastapi"],
        "doc_type": "deployment",
        "environment": "production",
        "last_updated": "2026-08-01",
        **meta,
    }
    return ScoredChunk(Chunk(cid, text, text, metadata), score)


def test_header_exposes_metadata():
    header = format_header(1, _sc("aws", "x"))
    assert header == (
        "[1] Fuente: docs/aws.md § Variables | stack: aws, fastapi | tipo: deployment | "
        "entorno: production | actualizado: 2026-08-01"
    )


def test_context_numbering_and_dedup():
    chunks = [_sc("a", "texto uno"), _sc("b", "texto  uno"), _sc("c", "texto dos")]
    ctx = build_context(chunks, max_tokens=1000)
    assert [c.chunk_id for c in ctx.used] == ["a", "c"]
    assert "[1] Fuente: docs/a.md" in ctx.text and "[2] Fuente: docs/c.md" in ctx.text


def test_context_token_budget_keeps_first():
    long_text = "palabra " * 500
    chunks = [_sc("a", long_text), _sc("b", "corto")]
    ctx = build_context(chunks, max_tokens=50)
    assert [c.chunk_id for c in ctx.used] == ["a"]  # the first one is kept even if it exceeds the budget


def test_extract_citations():
    used = [_sc("a", "x"), _sc("b", "y"), _sc("c", "z")]
    cits = extract_citations("Uno [3]. Dos [1][3]. Tres [2, 3]. Inválida [9].", used)
    assert [c.index for c in cits] == [3, 1, 2]
    assert cits[0].filepath == "docs/c.md"


def test_prompts_contain_faithfulness_rules():
    assert ABSTENTION_MESSAGE in SYSTEM_PROMPT
    assert "ÚNICAMENTE" in SYSTEM_PROMPT
    prompt = build_user_prompt("  ¿pregunta?  ", "CTX")
    assert "<contexto>\nCTX\n</contexto>" in prompt and "Pregunta: ¿pregunta?" in prompt
    assert is_abstention("No cuento con informacion suficiente en la documentación interna.")
    assert not is_abstention("PAYMENTS_DB_URL viene de Secrets Manager [1].")
