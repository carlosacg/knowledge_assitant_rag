"""Modelos de dominio compartidos por ingesta, recuperación y generación."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

RetrievalMode = Literal["dense", "bm25", "hybrid", "hybrid_rerank"]
RETRIEVAL_MODES: tuple[str, ...] = ("dense", "bm25", "hybrid", "hybrid_rerank")

_POINT_NAMESPACE = uuid.UUID("5b0c4a5e-1f1e-4d2a-9a57-6f1f3c7e2b10")


@dataclass(frozen=True)
class Document:
    filepath: str
    content: str
    metadata: dict[str, Any]

    @property
    def title(self) -> str:
        return self.metadata.get("title", self.filepath)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str  # texto original que ve el LLM / usuario
    embed_text: str  # breadcrumb + texto: lo que se vectoriza y se rerankea
    metadata: dict[str, Any]

    @property
    def point_id(self) -> str:
        return str(uuid.uuid5(_POINT_NAMESPACE, self.chunk_id))

    def to_payload(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "text": self.text, "embed_text": self.embed_text, **self.metadata}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Chunk:
        data = dict(payload)
        return cls(
            chunk_id=data.pop("chunk_id"),
            text=data.pop("text"),
            embed_text=data.pop("embed_text", ""),
            metadata=data,
        )


@dataclass
class ScoredChunk:
    chunk: Chunk
    score: float
    scores: dict[str, float] = field(default_factory=dict)  # trazabilidad por etapa

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id


@dataclass(frozen=True)
class SearchFilters:
    tech_stack: tuple[str, ...] | None = None  # match any
    environment: str | None = None  # siempre incluye docs con environment="all"
    doc_type: str | None = None

    @classmethod
    def build(
        cls,
        tech_stack: list[str] | tuple[str, ...] | None = None,
        environment: str | None = None,
        doc_type: str | None = None,
    ) -> SearchFilters | None:
        stack = tuple(s.lower() for s in tech_stack) if tech_stack else None
        if not stack and not environment and not doc_type:
            return None
        return cls(
            tech_stack=stack,
            environment=environment.lower() if environment else None,
            doc_type=doc_type.lower() if doc_type else None,
        )

    def matches(self, metadata: dict[str, Any]) -> bool:
        if self.tech_stack and not set(self.tech_stack) & set(metadata.get("tech_stack", [])):
            return False
        if self.environment and metadata.get("environment") not in (self.environment, "all"):
            return False
        if self.doc_type and metadata.get("doc_type") != self.doc_type:
            return False
        return True


@dataclass(frozen=True)
class Citation:
    index: int
    filepath: str
    section: str
    title: str
    score: float


@dataclass
class Answer:
    question: str
    text: str
    citations: list[Citation]
    contexts: list[ScoredChunk]
    abstained: bool
    mode: str
    debug: dict[str, Any] = field(default_factory=dict)
