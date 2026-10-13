"""Reranking con Cross-Encoder (BAAI/bge-reranker-base).

El modelo es CPU-bound y no async: se invoca desde un thread (ver ``RAGPipeline``) con un
``CapacityLimiter`` dedicado para no saturar la CPU del worker.
"""

from __future__ import annotations

import logging
import threading
from typing import Protocol

logger = logging.getLogger(__name__)


class Reranker(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        max_length: int = 512,
        batch_size: int = 16,
        torch_threads: int | None = None,
    ) -> None:
        self.model_name = model_name
        self.max_length = max_length
        self.batch_size = batch_size
        self.torch_threads = torch_threads
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    import torch
                    from sentence_transformers import CrossEncoder

                    if self.torch_threads:
                        torch.set_num_threads(self.torch_threads)
                    logger.info("cargando reranker", extra={"model": self.model_name})
                    # num_labels=1 -> activación sigmoide por defecto: scores en [0, 1].
                    self._model = CrossEncoder(self.model_name, max_length=self.max_length, device="cpu")
        return self._model

    def warmup(self) -> None:
        self.score("warmup", ["warmup"])

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        model = self._load()
        scores = model.predict(
            [(query, text) for text in texts], batch_size=self.batch_size, show_progress_bar=False
        )
        return [float(s) for s in scores]
