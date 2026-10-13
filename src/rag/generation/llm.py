"""Cliente LLM (OpenAI Chat Completions) con límite de concurrencia."""

from __future__ import annotations

import asyncio
from typing import Protocol

from openai import AsyncOpenAI


class LLMClient(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class OpenAIChatLLM:
    def __init__(
        self,
        client: AsyncOpenAI,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 800,
        max_concurrency: int = 32,
    ) -> None:
        self._client = client
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Protege la cuota de la API (rate limits) bajo picos de tráfico.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def complete(self, system: str, user: str) -> str:
        async with self._semaphore:
            response = await self._client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        return (response.choices[0].message.content or "").strip()
