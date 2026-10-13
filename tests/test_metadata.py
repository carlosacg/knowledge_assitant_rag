from __future__ import annotations

from datetime import date

from rag.ingestion.loader import load_documents
from rag.ingestion.metadata import infer_tech_stack, normalize_metadata


def test_normalize_defaults_and_invalid_values():
    meta = normalize_metadata(
        {"tech_stack": "FastAPI, AWS", "environment": "qa", "doc_type": "otro", "last_updated": date(2026, 3, 1)},
        filepath="docs/infra/x.md",
        content="# Título real\ncontenido",
    )
    assert meta["tech_stack"] == ["aws", "fastapi"]
    assert meta["environment"] == "all"
    assert meta["doc_type"] == "standard"
    assert meta["last_updated"] == "2026-03-01"
    assert meta["title"] == "Título real"


def test_doc_type_inferred_from_folder_and_stack_from_content():
    meta = normalize_metadata({}, filepath="docs/infra/gcp.md", content="Despliegue en Cloud Run con PostgreSQL")
    assert meta["doc_type"] == "deployment"
    assert set(meta["tech_stack"]) == {"gcp", "postgresql"}
    assert meta["last_updated"] == "unknown"


def test_infer_tech_stack():
    assert infer_tech_stack("Usamos Django y RDS") == ["aws", "django", "postgresql"]


def test_loader_reads_frontmatter(tmp_path):
    docs = tmp_path / "docs" / "backend"
    docs.mkdir(parents=True)
    (docs / "api.md").write_text(
        "---\ntitle: API\ntech_stack: [fastapi]\ndoc_type: standard\nenvironment: production\n"
        "last_updated: 2026-04-02\n---\n# API\n\nTexto.",
        encoding="utf-8",
    )
    [doc] = load_documents(tmp_path / "docs")
    assert doc.filepath == "docs/backend/api.md"
    assert doc.metadata["environment"] == "production"
    assert doc.metadata["last_updated"] == "2026-04-02"
    assert not doc.content.startswith("---")
