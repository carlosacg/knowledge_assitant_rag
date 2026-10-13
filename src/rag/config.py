"""Configuración centralizada (variables de entorno / .env)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OpenAI ---
    openai_api_key: SecretStr | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    embedding_batch_size: int = 128
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 800
    eval_llm_model: str = "gpt-4o-mini"
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 2

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"  # ":memory:" = modo embebido (tests)
    qdrant_api_key: SecretStr | None = None
    qdrant_collection: str = "engineering_docs"
    qdrant_timeout_s: int = 10

    # --- Rutas ---
    docs_dir: Path = PROJECT_ROOT / "docs"
    data_dir: Path = PROJECT_ROOT / "data"

    # --- Chunking ---
    chunk_size_tokens: int = 400
    chunk_overlap_tokens: int = 60
    max_code_tokens: int = 600
    code_intro_max_tokens: int = 80

    # --- Recuperación ---
    top_k: int = 20
    top_n: int = 5
    rrf_k: int = 60
    reranker_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_max_length: int = 512
    reranker_batch_size: int = 16
    min_rerank_score: float = 0.02
    context_max_tokens: int = 3000

    # --- Concurrencia y timeouts (por worker) ---
    max_in_flight_requests: int = 64
    llm_concurrency: int = 32
    rerank_concurrency: int = 2
    cpu_concurrency: int = 8
    torch_threads: int = 2
    http_max_connections: int = 100
    http_max_keepalive: int = 20
    embedding_cache_size: int = 2048
    embedding_cache_ttl_s: int = 3600
    retrieval_timeout_s: float = 10.0
    rerank_timeout_s: float = 8.0
    llm_timeout_s: float = 45.0

    log_level: str = "INFO"

    @property
    def bm25_path(self) -> Path:
        return self.data_dir / "bm25_index.pkl"

    @property
    def golden_set_path(self) -> Path:
        return self.data_dir / "eval" / "golden_set.jsonl"

    @property
    def eval_output_dir(self) -> Path:
        return self.data_dir / "eval"

    def require_openai_key(self) -> str:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value():
            raise RuntimeError("OPENAI_API_KEY no está configurada (ver .env.example).")
        return self.openai_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()
