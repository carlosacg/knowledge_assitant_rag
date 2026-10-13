"""Contratos de la API (Pydantic v2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from rag.models import Answer, RetrievalMode, SearchFilters


class FiltersIn(BaseModel):
    tech_stack: list[str] | None = Field(default=None, examples=[["aws", "fastapi"]])
    environment: Literal["production", "staging"] | None = None
    doc_type: Literal["architecture", "standard", "runbook", "schema", "deployment"] | None = None

    def to_domain(self) -> SearchFilters | None:
        return SearchFilters.build(self.tech_stack, self.environment, self.doc_type)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000, examples=["¿De dónde sale PAYMENTS_DB_URL en producción?"])
    filters: FiltersIn | None = None
    mode: RetrievalMode = "hybrid_rerank"
    debug: bool = False


class CitationOut(BaseModel):
    index: int
    filepath: str
    section: str
    title: str
    score: float


class AskResponse(BaseModel):
    answer: str
    abstained: bool
    citations: list[CitationOut]
    mode: str
    latency_ms: float
    request_id: str
    debug: dict[str, Any] | None = None

    @classmethod
    def from_answer(cls, answer: Answer, *, latency_ms: float, request_id: str, debug: bool) -> AskResponse:
        return cls(
            answer=answer.text,
            abstained=answer.abstained,
            citations=[CitationOut(**c.__dict__) for c in answer.citations],
            mode=answer.mode,
            latency_ms=latency_ms,
            request_id=request_id,
            debug=answer.debug if debug else None,
        )


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "unavailable"]
    checks: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: dict[str, str]
