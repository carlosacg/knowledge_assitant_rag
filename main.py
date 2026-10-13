"""CLI del asistente: ingest | ask | eval | serve."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import typer  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

from rag.config import get_settings  # noqa: E402
from rag.log import configure_logging  # noqa: E402
from rag.models import RETRIEVAL_MODES, Answer, SearchFilters  # noqa: E402

app = typer.Typer(add_completion=False, help="Engineering Knowledge RAG", no_args_is_help=True)
console = Console()


def _validate_mode(mode: str) -> str:
    if mode not in RETRIEVAL_MODES:
        raise typer.BadParameter(f"modo inválido; opciones: {', '.join(RETRIEVAL_MODES)}")
    return mode


@app.command()
def ingest(
    rebuild: Annotated[bool, typer.Option("--rebuild/--no-rebuild", help="Recrear la colección")] = True,
) -> None:
    """Chunking + metadata + embeddings -> Qdrant, y construcción del índice BM25."""
    from rag.clients import create_openai_client, create_qdrant_client
    from rag.ingestion.indexer import run_ingestion
    from rag.retrieval.embeddings import OpenAIEmbedder
    from rag.retrieval.vector_store import QdrantVectorStore

    settings = get_settings()
    configure_logging(settings.log_level, json_output=False)

    async def _run():
        openai_client = create_openai_client(settings)
        qdrant = create_qdrant_client(settings)
        try:
            embedder = OpenAIEmbedder(
                openai_client, settings.embedding_model, settings.embedding_dim, settings.embedding_batch_size
            )
            store = QdrantVectorStore(qdrant, settings.qdrant_collection, settings.embedding_dim)
            return await run_ingestion(settings, embedder, store, rebuild=rebuild)
        finally:
            await qdrant.close()
            await openai_client.close()

    stats = asyncio.run(_run())
    table = Table(title=f"Ingesta: {stats.documents} documentos -> {stats.chunks} chunks")
    table.add_column("Archivo")
    table.add_column("Chunks", justify="right")
    for filepath, n in sorted(stats.by_file.items()):
        table.add_row(filepath, str(n))
    console.print(table)
    console.print(f"Por tipo de contenido: {stats.by_content_type}")
    console.print(f"Índice BM25: {stats.bm25_path}")


def _print_answer(answer: Answer, debug: bool) -> None:
    style = "yellow" if answer.abstained else "green"
    console.print(Panel(Markdown(answer.text), title=f"Respuesta ({answer.mode})", border_style=style))
    if answer.citations:
        table = Table(title="Fuentes citadas")
        for col in ("#", "Archivo", "Sección", "Score"):
            table.add_column(col)
        for c in answer.citations:
            table.add_row(str(c.index), c.filepath, c.section or "-", f"{c.score:.4f}")
        console.print(table)
    if debug:
        for stage in ("dense", "bm25", "rrf", "rerank"):
            rows = answer.debug.get("retrieval", {}).get(stage)
            if not isinstance(rows, list):
                continue
            table = Table(title=f"Ranking: {stage}")
            for col in ("rank", "filepath", "section", "score"):
                table.add_column(col)
            for r in rows:
                table.add_row(str(r["rank"]), r["filepath"], r["section"] or "-", str(r["score"]))
            console.print(table)
        extra = {k: v for k, v in answer.debug.items() if k != "retrieval"}
        console.print(f"[dim]{extra}[/dim]")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Pregunta técnica")],
    stack: Annotated[list[str] | None, typer.Option("--stack", help="Filtro tech_stack (repetible)")] = None,
    env: Annotated[str | None, typer.Option("--env", help="production | staging")] = None,
    doc_type: Annotated[str | None, typer.Option("--doc-type")] = None,
    mode: Annotated[str, typer.Option("--mode", callback=_validate_mode)] = "hybrid_rerank",
    debug: Annotated[bool, typer.Option("--debug", help="Mostrar rankings por etapa")] = False,
) -> None:
    """Consulta el asistente (hybrid search + rerank + LLM con citas)."""
    from rag.resources import open_resources

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    filters = SearchFilters.build(stack, env, doc_type)

    async def _run() -> Answer:
        async with open_resources(settings, load_reranker=settings.reranker_enabled and mode == "hybrid_rerank") as res:
            return await res.pipeline.ask(question, mode=mode, filters=filters)

    _print_answer(asyncio.run(_run()), debug)


@app.command("eval")
def evaluate(
    modes: Annotated[str, typer.Option(help="Modos separados por coma o 'all'")] = "all",
    ragas: Annotated[bool, typer.Option("--ragas/--no-ragas", help="Incluir métricas Ragas (LLM-judge)")] = True,
    concurrency: Annotated[int, typer.Option(min=1, max=32)] = 4,
) -> None:
    """Evalúa retrieval (Hit/Recall/MRR/nDCG por modo), abstención y Ragas."""
    from rag.evaluation.run_eval import run_evaluation

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    selected = list(RETRIEVAL_MODES) if modes == "all" else [_validate_mode(m.strip()) for m in modes.split(",")]
    results = run_evaluation(settings, selected, with_ragas=ragas, concurrency=concurrency)

    retrieval = results["retrieval"]
    metric_names = list(next(iter(retrieval.values()))["summary"].keys())
    table = Table(title="Métricas de recuperación (preguntas con respuesta)")
    table.add_column("modo")
    for name in metric_names:
        table.add_column(name, justify="right")
    for mode_name, data in retrieval.items():
        table.add_row(mode_name, *(f"{data['summary'][n]:.3f}" for n in metric_names))
    console.print(table)

    console.print(Panel(str(results["generation"]["abstention"]), title="Abstención / citas (hybrid_rerank)"))
    if "ragas" in results:
        ragas_table = Table(title="Ragas (hybrid_rerank)")
        ragas_table.add_column("métrica")
        ragas_table.add_column("valor", justify="right")
        for name, value in results["ragas"]["summary"].items():
            ragas_table.add_row(name, f"{value:.3f}")
        console.print(ragas_table)
    console.print(f"Resultados guardados en {results['output_path']}")


@app.command()
def serve(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: Annotated[int, typer.Option(min=1)] = 1,
    reload: bool = False,
) -> None:
    """Servidor de desarrollo (en Docker se usa gunicorn + uvicorn workers)."""
    import uvicorn

    uvicorn.run("rag.api.app:app", host=host, port=port, workers=workers, reload=reload, app_dir="src")


if __name__ == "__main__":
    app()
