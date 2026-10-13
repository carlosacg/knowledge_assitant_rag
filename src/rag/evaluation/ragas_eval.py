"""End-to-end evaluation with Ragas: Context Precision, Context Recall, Faithfulness, Answer Relevancy.

Ragas and LangChain are imported lazily: they are not needed to serve the API or for the
unit tests. ``evaluate`` is synchronous and manages its own event loop, so it is called
outside ``asyncio.run``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from rag.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class RagasSample:
    user_input: str
    retrieved_contexts: list[str]
    response: str
    reference: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_input": self.user_input,
            "retrieved_contexts": self.retrieved_contexts or [""],
            "response": self.response,
            "reference": self.reference,
        }


def run_ragas(samples: list[RagasSample], settings: Settings) -> tuple[dict[str, float], list[dict[str, Any]]]:
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import EvaluationDataset, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    api_key = settings.require_openai_key()
    judge = LangchainLLMWrapper(ChatOpenAI(model=settings.eval_llm_model, temperature=0, api_key=api_key))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings(model=settings.embedding_model, api_key=api_key))

    dataset = EvaluationDataset.from_list([s.as_dict() for s in samples])
    result = evaluate(
        dataset=dataset,
        metrics=[LLMContextPrecisionWithReference(), LLMContextRecall(), Faithfulness(), ResponseRelevancy()],
        llm=judge,
        embeddings=embeddings,
        show_progress=True,
        raise_exceptions=False,
    )
    df = result.to_pandas()
    numeric = df.select_dtypes("number")
    summary = {col: round(float(numeric[col].mean(skipna=True)), 4) for col in numeric.columns}
    per_sample = df.drop(columns=["retrieved_contexts"], errors="ignore").to_dict(orient="records")
    return summary, per_sample
