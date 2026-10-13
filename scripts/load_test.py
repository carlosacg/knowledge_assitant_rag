"""Prueba de carga concurrente contra POST /ask.

Uso (desde el host o dentro de la red de compose):
    python scripts/load_test.py --url http://localhost:8000 --concurrency 50 --requests 200
    docker compose run --rm api python scripts/load_test.py --url http://api:8000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections import Counter

import httpx

QUESTIONS = [
    "¿De dónde obtiene payments-service la variable PAYMENTS_DB_URL en producción?",
    "¿Qué scope OAuth requiere el endpoint POST /api/v1/payments/refunds?",
    "¿Cuántos reintentos tiene una cola SQS antes de enviar el mensaje a la DLQ?",
    "¿Cómo se ejecutan las migraciones antes de un deploy a producción?",
    "¿Qué hacer cuando se dispara la alerta PaymentsDBHighConnections?",
    "¿Cómo se estructura un proyecto FastAPI según los estándares internos?",
    "¿Qué valor tiene PAYMENTS_DB_POOL_SIZE en staging?",
    "¿Qué versión de Kubernetes usa el cluster de Azure AKS?",
]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct / 100 * len(ordered)) - 1))
    return ordered[idx]


async def run(url: str, total: int, concurrency: int, timeout: float) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter[str] = Counter()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(base_url=url, timeout=timeout, limits=limits) as client:

        async def one(i: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    resp = await client.post("/ask", json={"question": random.choice(QUESTIONS)})
                    statuses[str(resp.status_code)] += 1
                    if resp.status_code == 200:
                        latencies.append((time.perf_counter() - started) * 1000)
                except httpx.HTTPError as exc:
                    statuses[type(exc).__name__] += 1

        started = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(total)))
        wall = time.perf_counter() - started

    print(f"\nRequests: {total} | concurrencia: {concurrency} | duración: {wall:.1f}s")
    print(f"Throughput: {total / wall:.1f} req/s (OK: {len(latencies) / wall:.1f} req/s)")
    print(f"Estados: {dict(statuses)}")
    if latencies:
        print(
            f"Latencia OK (ms): p50={percentile(latencies, 50):.0f} p95={percentile(latencies, 95):.0f} "
            f"p99={percentile(latencies, 99):.0f} media={statistics.mean(latencies):.0f}"
        )
    unexpected = {k: v for k, v in statuses.items() if k not in ("200", "503")}
    if unexpected:
        print(f"ATENCIÓN: respuestas inesperadas {unexpected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.requests, args.concurrency, args.timeout))


if __name__ == "__main__":
    main()
