"""Construcción estructurada del contexto: fragmentos numerados con metadatos visibles."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from rag.models import Citation, ScoredChunk
from rag.tokens import count_tokens

_CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True)
class BuiltContext:
    text: str
    used: list[ScoredChunk]  # used[i] corresponde a la cita [i + 1]
    tokens: int


def format_header(index: int, item: ScoredChunk) -> str:
    m = item.chunk.metadata
    source = m.get("filepath", "desconocido")
    if m.get("section"):
        source += f" § {m['section']}"
    stack = ", ".join(m.get("tech_stack", [])) or "n/a"
    return (
        f"[{index}] Fuente: {source} | stack: {stack} | tipo: {m.get('doc_type', 'n/a')} | "
        f"entorno: {m.get('environment', 'n/a')} | actualizado: {m.get('last_updated', 'n/a')}"
    )


def build_context(chunks: Sequence[ScoredChunk], max_tokens: int = 3000) -> BuiltContext:
    """Ensambla el contexto respetando el orden de relevancia y un presupuesto de tokens.

    Se descartan duplicados (mismo chunk o mismo texto). El primer fragmento siempre entra,
    aunque exceda el presupuesto, para no dejar al LLM sin evidencia.
    """
    blocks: list[str] = []
    used: list[ScoredChunk] = []
    seen: set[str] = set()
    total = 0
    for item in chunks:
        key = " ".join(item.chunk.text.split())
        if item.chunk_id in seen or key in seen:
            continue
        block = f"{format_header(len(used) + 1, item)}\n{item.chunk.text}"
        n = count_tokens(block)
        if used and total + n > max_tokens:
            continue  # puede entrar un fragmento posterior más corto
        seen.update({item.chunk_id, key})
        blocks.append(block)
        used.append(item)
        total += n
    return BuiltContext(text="\n\n---\n\n".join(blocks), used=used, tokens=total)


def extract_citations(answer: str, used: Sequence[ScoredChunk]) -> list[Citation]:
    """Devuelve las fuentes efectivamente citadas ([n]) en orden de primera aparición."""
    citations: list[Citation] = []
    seen: set[int] = set()
    for match in _CITATION_RE.finditer(answer):
        for raw in match.group(1).split(","):
            idx = int(raw.strip())
            if idx in seen or not 1 <= idx <= len(used):
                continue
            seen.add(idx)
            m = used[idx - 1].chunk.metadata
            citations.append(
                Citation(
                    index=idx,
                    filepath=m.get("filepath", ""),
                    section=m.get("section", ""),
                    title=m.get("title", ""),
                    score=round(used[idx - 1].score, 4),
                )
            )
    return citations
