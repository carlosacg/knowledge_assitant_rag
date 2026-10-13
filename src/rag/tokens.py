"""Token counting with tiktoken (with an approximate fallback if the encoding is unavailable)."""

from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

_ENCODING_NAME = "cl100k_base"  # encoding used by text-embedding-3-*


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:  # no network and no tiktoken cache
        logger.warning("tiktoken no disponible; se usa conteo aproximado (len/4).")
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text, disallowed_special=()))
