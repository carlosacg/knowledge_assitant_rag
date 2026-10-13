"""Prompts with faithfulness constraints (anti-hallucination)."""

from __future__ import annotations

import unicodedata

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


def build_user_prompt(question: str, context: str) -> str:
    return USER_TEMPLATE.format(context=context, question=question.strip())


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_ABSTENTION_KEY = _normalize("No cuento con información suficiente")


def is_abstention(text: str) -> bool:
    return _ABSTENTION_KEY in _normalize(text)
