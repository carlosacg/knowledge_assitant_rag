"""Production gunicorn configuration (async uvicorn workers)."""

import os

bind = os.getenv("BIND", "0.0.0.0:8000")
# Each worker loads the reranker (~1 GB RAM): size according to available memory.
workers = int(os.getenv("WEB_CONCURRENCY", "2"))
worker_class = "uvicorn_worker.UvicornWorker"
# No preload: torch and the async clients must not be shared across processes after fork.
preload_app = False
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))  # includes the initial model load
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))
# Periodic worker recycling to bound memory fragmentation.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "5000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "500"))
accesslog = None  # the access log is emitted by RequestContextMiddleware (JSON)
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
