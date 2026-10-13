"""Dataset de evaluación (golden set) con preguntas, respuestas de referencia y fuentes relevantes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class GoldenItem(BaseModel):
    id: str
    question: str
    ground_truth: str
    relevant_sources: list[str] = Field(default_factory=list)
    answerable: bool = True
    category: Literal["conceptual", "exact_identifier", "multi_doc", "procedural", "unanswerable"] = "conceptual"


def load_golden_set(path: Path) -> list[GoldenItem]:
    items: list[GoldenItem] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                items.append(GoldenItem.model_validate(json.loads(line)))
            except Exception as exc:
                raise ValueError(f"{path}:{lineno}: entrada inválida ({exc})") from exc
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{path}: ids duplicados en el golden set")
    return items
