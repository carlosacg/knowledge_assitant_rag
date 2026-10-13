from __future__ import annotations

import re

from rag.ingestion.chunker import MarkdownChunker, parse_sections, split_code, split_prose
from rag.models import Document
from tests.conftest import word_count

CODE_LINES = "\n".join(f"    step_{i} = compute(value_{i})  # paso {i}" for i in range(120))

MARKDOWN = f"""# Guía de Pagos

Introducción general del servicio de pagos.

## Implementación

{" ".join(f"Oración número {i} sobre el flujo de reembolsos." for i in range(40))}

El siguiente módulo implementa el flujo completo:

```python
# Esto es un comentario, NO un encabezado
def refund_flow(value_0):
{CODE_LINES}
    return value_0
```

### Configuración

```bash
# export PAYMENTS_DB_URL=...
export LOG_LEVEL=INFO
```
"""

DOC = Document(
    filepath="docs/architecture/payments.md",
    content=MARKDOWN,
    metadata={
        "title": "Guía de Pagos",
        "filepath": "docs/architecture/payments.md",
        "tech_stack": ["fastapi", "postgresql"],
        "doc_type": "architecture",
        "environment": "all",
        "last_updated": "2026-09-01",
    },
)


def make_chunker() -> MarkdownChunker:
    return MarkdownChunker(chunk_size=50, chunk_overlap=10, max_code_tokens=150, intro_max_tokens=20, counter=word_count)


def _fences(text: str) -> int:
    return sum(1 for line in text.splitlines() if re.match(r"^\s*```", line))


def test_headings_inside_code_are_not_sections():
    sections = parse_sections(MARKDOWN)
    paths = [" > ".join(s.headings) for s in sections]
    assert paths == ["Guía de Pagos", "Guía de Pagos > Implementación", "Guía de Pagos > Implementación > Configuración"]
    assert not any("comentario" in p or "PAYMENTS_DB_URL" in p for p in paths)


def test_code_blocks_never_broken():
    chunks = make_chunker().chunk(DOC)
    code_chunks = [c for c in chunks if c.metadata["content_type"] == "code"]
    assert len(code_chunks) >= 3  # el bloque largo se divide en varias partes + el bloque bash
    for chunk in chunks:
        assert _fences(chunk.text) % 2 == 0, f"fence desbalanceado en {chunk.chunk_id}"
    for chunk in code_chunks:
        code_part = chunk.text[chunk.text.index("```") :]
        assert code_part.startswith("```python") or code_part.startswith("```bash")
        # Ninguna línea de código queda cortada a la mitad.
        for line in code_part.splitlines()[1:-1]:
            assert line == "" or line.startswith(("    ", "def ", "# ", "export"))


def test_prose_respects_size_and_overlap():
    chunks = make_chunker().chunk(DOC)
    prose = [c for c in chunks if c.metadata["content_type"] == "prose" and c.metadata["section"] == "Implementación"]
    assert len(prose) >= 3
    for chunk in prose:
        assert word_count(chunk.text) <= 50
    for prev, nxt in zip(prose, prose[1:]):
        tail = prev.text.split()[-3:]
        assert " ".join(tail) in nxt.text, "debe existir solapamiento entre chunks consecutivos"


def test_intro_paragraph_attached_to_code():
    chunks = make_chunker().chunk(DOC)
    first_code = next(c for c in chunks if c.metadata["content_type"] == "code")
    assert first_code.text.startswith("El siguiente módulo implementa el flujo completo:")
    prose_texts = " ".join(c.text for c in chunks if c.metadata["content_type"] == "prose")
    assert "El siguiente módulo implementa" not in prose_texts


def test_breadcrumb_and_metadata_propagated():
    chunks = make_chunker().chunk(DOC)
    config = next(c for c in chunks if c.metadata["section"] == "Implementación > Configuración")
    assert config.embed_text.startswith("Guía de Pagos > Implementación > Configuración\n\n")
    assert config.metadata["language"] == "bash"
    for chunk in chunks:
        assert chunk.metadata["tech_stack"] == ["fastapi", "postgresql"]
        assert chunk.metadata["filepath"] == "docs/architecture/payments.md"
        assert chunk.metadata["last_updated"] == "2026-09-01"
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_chunk_ids_are_deterministic():
    assert [c.chunk_id for c in make_chunker().chunk(DOC)] == [c.chunk_id for c in make_chunker().chunk(DOC)]


def test_split_code_small_block_untouched():
    code = "```sql\nSELECT 1;\n```"
    assert split_code(code, 100, word_count) == [code]


def test_split_prose_short_text_single_chunk():
    assert split_prose("uno dos tres", 10, 2, word_count) == ["uno dos tres"]
    assert split_prose("   ", 10, 2, word_count) == []
