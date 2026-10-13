"""Chunking adaptativo de Markdown: distingue bloques de código de prosa.

Estrategia:
1. Se divide el documento por encabezados -> secciones con *breadcrumb* (Doc > Sección > Sub).
2. Dentro de cada sección se separan segmentos de prosa y bloques de código cercados (```).
3. Código: unidad atómica. Solo se divide si excede ``max_code_tokens`` y siempre por líneas,
   reabriendo/cerrando el fence en cada parte. El párrafo corto que lo introduce se le adjunta.
4. Prosa: splitter recursivo por tokens (párrafo -> línea -> oración -> palabra) con solapamiento.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from rag.models import Chunk, Document
from rag.tokens import count_tokens

TokenCounter = Callable[[str], int]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_OPEN_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([\w+#.-]*)")

# (regex de corte, string para volver a unir) — de más gruesa a más fina.
_PROSE_SEPARATORS: tuple[tuple[str, str], ...] = (
    (r"\n\s*\n", "\n\n"),
    (r"\n", "\n"),
    (r"(?<=[.!?:;])\s+", " "),
    (r"\s+", " "),
)


@dataclass
class Segment:
    kind: str  # "prose" | "code"
    text: str
    language: str | None = None
    intro: str | None = None  # párrafo de prosa que introduce un bloque de código


@dataclass
class Section:
    headings: list[str]
    segments: list[Segment] = field(default_factory=list)


# --------------------------------------------------------------------------- parsing


def _is_fence_close(line: str, fence: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and set(stripped) == {fence[0]} and len(stripped) >= len(fence)


def parse_sections(markdown: str) -> list[Section]:
    """Parte el Markdown en secciones (por encabezado) con segmentos de prosa/código."""
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    segments: list[Segment] = []
    prose: list[str] = []
    code: list[str] = []
    fence: str | None = None
    language: str | None = None

    def flush_prose() -> None:
        text = "\n".join(prose).strip()
        if text:
            segments.append(Segment("prose", text))
        prose.clear()

    def flush_section() -> None:
        flush_prose()
        if segments:
            sections.append(Section([title for _, title in stack], list(segments)))
        segments.clear()

    for line in markdown.splitlines():
        if fence is not None:
            code.append(line)
            if _is_fence_close(line, fence):
                segments.append(Segment("code", "\n".join(code), language))
                code.clear()
                fence = None
            continue

        opened = _FENCE_OPEN_RE.match(line)
        if opened:
            flush_prose()
            fence, language = opened.group(1), (opened.group(2) or None)
            code.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush_section()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            continue

        prose.append(line)

    if fence is not None:  # bloque sin cerrar: se cierra para no producir fences rotos
        code.append(fence)
        segments.append(Segment("code", "\n".join(code), language))
    flush_section()
    return sections


# --------------------------------------------------------------------------- prosa


def _merge_splits(splits: list[str], joiner: str, size: int, overlap: int, count: TokenCounter) -> list[str]:
    """Une piezas atómicas en chunks de ``size`` tokens con ``overlap`` tokens de solapamiento."""
    chunks: list[str] = []
    window: list[tuple[str, int]] = []
    total = 0
    joiner_tokens = count(joiner) if joiner.strip() else 0

    for piece in splits:
        n = count(piece)
        extra = joiner_tokens if window else 0
        if window and total + extra + n > size:
            chunks.append(joiner.join(p for p, _ in window).strip())
            # Se conserva la cola de la ventana como solapamiento.
            while window and (total > overlap or total + joiner_tokens + n > size):
                _, first_n = window.pop(0)
                total -= first_n + (joiner_tokens if window else 0)
            extra = joiner_tokens if window else 0
        window.append((piece, n))
        total += n + extra

    if window:
        chunks.append(joiner.join(p for p, _ in window).strip())
    return [c for c in chunks if c]


def split_prose(
    text: str,
    size: int,
    overlap: int,
    count: TokenCounter = count_tokens,
    separators: tuple[tuple[str, str], ...] = _PROSE_SEPARATORS,
) -> list[str]:
    """Splitter recursivo por tokens (estilo RecursiveCharacterTextSplitter)."""
    text = text.strip()
    if not text:
        return []
    if count(text) <= size:
        return [text]

    for idx, (pattern, joiner) in enumerate(separators):
        parts = [p for p in re.split(pattern, text) if p.strip()]
        if len(parts) > 1:
            break
    else:
        return [text]  # sin separadores aplicables: se acepta sobredimensionado

    finer = separators[idx + 1 :]
    result: list[str] = []
    pending: list[str] = []
    for part in parts:
        if count(part) <= size:
            pending.append(part)
            continue
        if pending:
            result.extend(_merge_splits(pending, joiner, size, overlap, count))
            pending = []
        result.extend(split_prose(part, size, overlap, count, finer) if finer else [part])
    if pending:
        result.extend(_merge_splits(pending, joiner, size, overlap, count))
    return result


# --------------------------------------------------------------------------- código


def split_code(code: str, max_tokens: int, count: TokenCounter = count_tokens) -> list[str]:
    """Divide un bloque cercado solo si excede ``max_tokens``; cada parte queda con su fence."""
    if count(code) <= max_tokens:
        return [code]

    lines = code.splitlines()
    opener = lines[0]
    fence_match = _FENCE_OPEN_RE.match(opener)
    fence = fence_match.group(1) if fence_match else "```"
    has_close = len(lines) > 1 and _is_fence_close(lines[-1], fence)
    body = lines[1:-1] if has_close else lines[1:]
    closer = lines[-1] if has_close else fence
    budget = max(1, max_tokens - count(opener) - count(closer))

    # Bloques lógicos separados por líneas en blanco; si uno excede el presupuesto, por líneas.
    blocks: list[list[str]] = [[]]
    for line in body:
        if not line.strip() and blocks[-1]:
            blocks.append([])
        else:
            blocks[-1].append(line)
    # (texto, separador previo): "\n\n" entre bloques lógicos, "\n" entre líneas de un mismo bloque.
    units: list[tuple[str, str]] = []
    for block in filter(None, blocks):
        block_text = "\n".join(block)
        if count(block_text) <= budget:
            units.append((block_text, "\n\n"))
        else:
            units.extend((line, "\n\n" if i == 0 else "\n") for i, line in enumerate(block))

    parts: list[str] = []
    current = ""
    used = 0
    for text, sep in units:
        n = count(text) + 1
        if current and used + n > budget:
            parts.append(current)
            current, used = "", 0
        current = f"{current}{sep}{text}" if current else text
        used += n
    if current:
        parts.append(current)
    return [f"{opener}\n{part}\n{closer}" for part in parts]


# --------------------------------------------------------------------------- chunker


class MarkdownChunker:
    def __init__(
        self,
        chunk_size: int = 400,
        chunk_overlap: int = 60,
        max_code_tokens: int = 600,
        intro_max_tokens: int = 80,
        counter: TokenCounter = count_tokens,
    ) -> None:
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap debe ser menor que chunk_size")
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.max_code_tokens = max_code_tokens
        self.intro_max_tokens = intro_max_tokens
        self.count = counter

    def _attach_intros(self, segments: list[Segment]) -> None:
        """Mueve el último párrafo corto de la prosa previa a su bloque de código."""
        for prev, seg in zip(segments, segments[1:]):
            if seg.kind != "code" or prev.kind != "prose":
                continue
            paragraphs = re.split(r"\n\s*\n", prev.text)
            last = paragraphs[-1].strip()
            if last and self.count(last) <= self.intro_max_tokens:
                seg.intro = last
                prev.text = "\n\n".join(paragraphs[:-1]).strip()

    def chunk(self, doc: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in parse_sections(doc.content):
            headings = section.headings
            if headings and headings[0].strip().lower() == doc.title.strip().lower():
                headings = headings[1:]
            breadcrumb = " > ".join([doc.title, *headings])
            section_name = " > ".join(headings)

            self._attach_intros(section.segments)
            for seg in section.segments:
                if seg.kind == "prose":
                    pieces = split_prose(seg.text, self.chunk_size, self.chunk_overlap, self.count)
                    chunks.extend(
                        self._make_chunk(doc, len(chunks) + i, p, breadcrumb, section_name, "prose", None)
                        for i, p in enumerate(pieces)
                    )
                else:
                    for i, part in enumerate(split_code(seg.text, self.max_code_tokens, self.count)):
                        text = f"{seg.intro}\n\n{part}" if seg.intro and i == 0 else part
                        chunks.append(
                            self._make_chunk(doc, len(chunks), text, breadcrumb, section_name, "code", seg.language)
                        )
        return chunks

    def _make_chunk(
        self,
        doc: Document,
        index: int,
        text: str,
        breadcrumb: str,
        section: str,
        content_type: str,
        language: str | None,
    ) -> Chunk:
        digest = hashlib.sha1(f"{doc.filepath}|{index}|{text}".encode()).hexdigest()[:16]
        metadata = {
            **doc.metadata,
            "section": section,
            "breadcrumb": breadcrumb,
            "content_type": content_type,
            "language": language or "",
            "chunk_index": index,
            "token_count": self.count(text),
        }
        return Chunk(chunk_id=digest, text=text, embed_text=f"{breadcrumb}\n\n{text}", metadata=metadata)
