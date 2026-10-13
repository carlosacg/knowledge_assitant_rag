"""Búsqueda léxica BM25 con un tokenizer que preserva identificadores técnicos.

``PAYMENTS_DB_URL``, ``/api/v1/payments/refunds`` o ``payments.refund.requested`` se indexan
como token completo *y* por sus partes, de modo que tanto la consulta exacta como la parcial
("refunds", "db url") encuentran el fragmento.
"""

from __future__ import annotations

import pickle
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from rag.models import Chunk, ScoredChunk, SearchFilters

_TOKEN_RE = re.compile(r"\w[\w./:-]*\w|\w", re.UNICODE)
_SUBPART_RE = re.compile(r"[._/:-]+")
_INDEX_VERSION = 1

STOPWORDS = frozenset(
    """
    a al algo ante con como cual cuales cuando de del desde donde el ella en entre es esta este esto
    hay la las le les lo los mas me mi muy no nos o para pero por que se si sin sobre son su sus
    tambien te tiene un una uno unos y ya qué cómo cuál dónde cuándo está están
    the a an and are as at be by for from how in is it of on or that the to was what when where which
    with
    """.split()
)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw in STOPWORDS:
            continue
        tokens.append(raw)
        if _SUBPART_RE.search(raw):
            tokens.extend(p for p in _SUBPART_RE.split(raw) if len(p) > 1 and p not in STOPWORDS)
    return tokens


class BM25Index:
    """Índice BM25 en memoria (solo lectura tras construirse, seguro para lecturas concurrentes)."""

    def __init__(self, chunks: list[Chunk], tokenized: list[list[str]]) -> None:
        if not chunks:
            raise ValueError("No se puede construir un índice BM25 vacío")
        self.chunks = chunks
        self._tokenized = tokenized
        self._bm25 = BM25Okapi(tokenized)

    @classmethod
    def build(cls, chunks: list[Chunk]) -> BM25Index:
        return cls(chunks, [tokenize(c.embed_text) for c in chunks])

    def __len__(self) -> int:
        return len(self.chunks)

    def search(self, query: str, top_k: int, filters: SearchFilters | None = None) -> list[ScoredChunk]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        if filters is not None:
            mask = np.array([filters.matches(c.metadata) for c in self.chunks], dtype=bool)
            scores = np.where(mask, scores, -np.inf)
        order = np.argsort(-scores)[:top_k]
        return [
            ScoredChunk(self.chunks[i], float(scores[i]), {"bm25": float(scores[i])})
            for i in order
            if np.isfinite(scores[i]) and scores[i] > 0
        ]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _INDEX_VERSION,
            "chunks": [c.to_payload() for c in self.chunks],
            "tokenized": self._tokenized,
        }
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(path)  # escritura atómica: los workers nunca leen un archivo a medias

    @classmethod
    def load(cls, path: Path) -> BM25Index:
        with path.open("rb") as fh:
            payload = pickle.load(fh)  # archivo generado por nuestro propio ingest
        if payload.get("version") != _INDEX_VERSION:
            raise ValueError(f"Versión de índice BM25 incompatible: {payload.get('version')}")
        return cls([Chunk.from_payload(p) for p in payload["chunks"]], payload["tokenized"])
