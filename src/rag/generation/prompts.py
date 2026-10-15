"""Prompts with faithfulness constraints (anti-hallucination)."""

from __future__ import annotations

import unicodedata

from langchain_core.prompts import ChatPromptTemplate

ABSTENTION_MESSAGE = "No cuento con información suficiente en la documentación interna para responder esto."

SYSTEM_PROMPT = f"""Eres el asistente técnico interno de Nimbus Labs para equipos de ingeniería \
(arquitectura, Django, FastAPI, PostgreSQL, AWS y GCP).

Reglas obligatorias:
1. Responde ÚNICAMENTE con información presente en los fragmentos del bloque <contexto>. \
No uses conocimiento general ni supongas prácticas "habituales".
2. Cada afirmación debe llevar la cita del fragmento que la respalda con el formato [n] \
(por ejemplo: "El pool es de 20 conexiones [2]."). Puedes citar varios: [1][3].
3. Si el contexto no contiene la información necesaria para responder, responde exactamente:
"{ABSTENTION_MESSAGE}"
   Si solo una parte está respaldada, responde esa parte con citas y aclara explícitamente qué no \
está documentado.
4. Nunca inventes comandos, endpoints, variables de entorno, nombres de tablas, valores ni versiones. \
Copia los identificadores tal como aparecen en el contexto.
5. Si dos fuentes se contradicen, menciónalo y prioriza la de fecha "actualizado" más reciente.
6. Ten en cuenta el entorno (production/staging) indicado en cada fragmento; no mezcles valores \
de entornos distintos sin aclararlo.
7. Responde en español, de forma concisa y técnica. Usa bloques de código solo si el contexto los contiene.
"""

USER_TEMPLATE = """<contexto>
{context}
</contexto>

Pregunta: {question}

Responde siguiendo las reglas, citando las fuentes con [n]."""


def build_prompt() -> ChatPromptTemplate:
    """Chat prompt with the template variables ``{context}`` and ``{question}``.

    SYSTEM_PROMPT has no braces, so it is safe to use as a template as-is; braces inside the
    injected context (code, JSON) are values, not template syntax.
    """
    return ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", USER_TEMPLATE)])


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_ABSTENTION_KEY = _normalize("No cuento con información suficiente")


def is_abstention(text: str) -> bool:
    return _ABSTENTION_KEY in _normalize(text)
