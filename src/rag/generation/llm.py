"""Answer generation as an LCEL chain (prompt | chat model | parser) with a concurrency limit."""

from __future__ import annotations

import asyncio

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser

from rag.generation.prompts import build_prompt


class AnswerGenerator:
    """Runs ``build_prompt() | chat_model | StrOutputParser()``.

    ``chat_model`` is any LangChain chat model: ``ChatOpenAI`` in production, a fake in tests.
    """

    def __init__(self, chat_model: BaseChatModel, max_concurrency: int = 32) -> None:
        self.chat_model = chat_model
        self.chain = build_prompt() | chat_model | StrOutputParser()
        # Protects the API quota (rate limits) during traffic spikes.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def agenerate(self, question: str, context: str) -> str:
        async with self._semaphore:
            text = await self.chain.ainvoke({"question": question.strip(), "context": context})
        return text.strip()
