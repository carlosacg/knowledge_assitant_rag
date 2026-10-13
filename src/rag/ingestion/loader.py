"""Loading of Markdown documents with YAML frontmatter."""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from rag.ingestion.metadata import normalize_metadata
from rag.models import Document

logger = logging.getLogger(__name__)


def load_document(path: Path, root: Path) -> Document:
    post = frontmatter.load(path)
    filepath = path.relative_to(root).as_posix()
    metadata = normalize_metadata(dict(post.metadata), filepath=filepath, content=post.content)
    return Document(filepath=filepath, content=post.content, metadata=metadata)


def load_documents(docs_dir: Path) -> list[Document]:
    """Loads every ``*.md`` under ``docs_dir``. The filepath is relative to its parent (``docs/...``)."""
    if not docs_dir.is_dir():
        raise FileNotFoundError(f"No existe el directorio de documentación: {docs_dir}")
    root = docs_dir.parent
    docs = [load_document(p, root) for p in sorted(docs_dir.rglob("*.md"))]
    logger.info("documentos cargados", extra={"count": len(docs), "docs_dir": str(docs_dir)})
    return docs
