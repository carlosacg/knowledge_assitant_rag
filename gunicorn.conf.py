"""Configuración de gunicorn para producción (workers uvicorn async)."""

import os

bind = os.getenv("BIND", "0.0.0.0:8000")
# Cada worker carga el reranker (~1 GB RAM): dimensionar según memoria disponible.
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn_worker.UvicornWorker"
# Sin preload: torch y los clientes async no deben compartirse entre procesos tras fork.
preload_app = False
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))  # incluye la carga inicial del modelo
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
# Reciclado periódico de workers para acotar fragmentación de memoria.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "500"))
accesslog = None  # el log de acceso lo emite RequestContextMiddleware (JSON)
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
