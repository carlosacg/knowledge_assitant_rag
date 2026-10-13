"""Structured context building: numbered chunks with visible metadata."""

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
    used: list[ScoredChunk]  # used[i] maps to citation [i + 1]
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
    """Assembles the context respecting relevance order and a token budget.

    Duplicates (same chunk or same text) are dropped. The first chunk is always included,
    even if it exceeds the budget, so the LLM is never left without evidence.
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
            continue  # a later, shorter chunk may still fit
        seen.update({item.chunk_id, key})
        blocks.append(block)
        used.append(item)
        total += n
    return BuiltContext(text="\n\n---\n\n".join(blocks), used=used, tokens=total)


def extract_citations(answer: str, used: Sequence[ScoredChunk]) -> list[Citation]:
    """Returns the sources actually cited ([n]) in order of first appearance."""
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
