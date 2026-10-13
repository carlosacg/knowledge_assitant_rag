"""Document-level metadata normalization and enrichment."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

VALID_DOC_TYPES = {"architecture", "standard", "runbook", "schema", "deployment"}
VALID_ENVIRONMENTS = {"production", "staging", "all"}

# tech_stack inference when the frontmatter does not declare it.
_STACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "django": ("django", "drf", "manage.py"),
    "fastapi": ("fastapi", "pydantic", "uvicorn"),
    "postgresql": ("postgresql", "postgres", "pg_stat", "rds"),
    "aws": ("aws", "ecs", "fargate", "rds", "sqs", "sns", "secrets manager"),
    "gcp": ("gcp", "cloud run", "gke", "pub/sub", "bigquery", "cloud sql"),
    "python": ("python",),
}

# Docs folder -> default doc_type.
_FOLDER_DOC_TYPE = {"architecture": "architecture", "backend": "standard", "database": "schema", "infra": "deployment"}

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def infer_tech_stack(text: str) -> list[str]:
    lowered = text.lower()
    return sorted(stack for stack, kws in _STACK_KEYWORDS.items() if any(kw in lowered for kw in kws))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    return sorted({str(v).strip().lower() for v in value if str(v).strip()})


def _as_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_metadata(raw: dict[str, Any], *, filepath: str, content: str) -> dict[str, Any]:
    """Returns a document's canonical metadata (lowercased values, defaults and inferences)."""
    parts = PurePosixPath(filepath).parts
    folder = parts[-2] if len(parts) >= 2 else ""

    title = str(raw.get("title") or "").strip()
    if not title:
        h1 = _H1_RE.search(content)
        title = h1.group(1) if h1 else PurePosixPath(filepath).stem.replace("-", " ").title()

    tech_stack = _as_list(raw.get("tech_stack")) or infer_tech_stack(content)

    doc_type = str(raw.get("doc_type") or _FOLDER_DOC_TYPE.get(folder, "standard")).lower()
    if doc_type not in VALID_DOC_TYPES:
        doc_type = "standard"

    environment = str(raw.get("environment") or "all").lower()
    if environment not in VALID_ENVIRONMENTS:
        environment = "all"

    return {
        "title": title,
        "filepath": filepath,
        "tech_stack": tech_stack,
        "doc_type": doc_type,
        "environment": environment,
        "last_updated": _as_date(raw.get("last_updated")) or "unknown",
        "owner": str(raw.get("owner") or "unknown"),
    }
