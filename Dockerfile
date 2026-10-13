# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------- builder
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH

RUN python -m venv /opt/venv

WORKDIR /build
# CPU torch first (from the PyTorch index): avoids ~2 GB of CUDA dependencies.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.5.1"
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt -r requirements-dev.txt

# Pre-download model artifacts: the container starts without depending on the network.
ENV HF_HOME=/models/hf \
    TIKTOKEN_CACHE_DIR=/models/tiktoken
ARG RERANKER_MODEL=BAAI/bge-reranker-base
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')" && \
    python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKER_MODEL}', device='cpu')"

# ---------------------------------------------------------------- runtime
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/src \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TIKTOKEN_CACHE_DIR=/models/tiktoken \
    OMP_NUM_THREADS=2 \
    TOKENIZERS_PARALLELISM=false

RUN useradd --create-home --uid 1000 app

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=app:app /models /models

WORKDIR /app
COPY --chown=app:app . .
RUN mkdir -p /app/data && chown app:app /app/data

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["gunicorn", "rag.api.app:app", "-c", "gunicorn.conf.py"]
