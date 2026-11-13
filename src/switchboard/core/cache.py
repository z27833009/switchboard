"""Query normalization for the semantic cache (design.md section 10.2).

The stored ``query_text`` and the cache key carry a normalized form of the
question so two requests that differ only in case or whitespace are visibly the
same entry. Matching itself still uses the embedding; normalization never
changes the text that is embedded, because retrieval on a miss must see the
question as the user wrote it.
"""

from __future__ import annotations

import unicodedata


def normalize_query(text: str) -> str:
    """NFKC, trimmed, lowercase, with runs of whitespace collapsed to one space."""
    return " ".join(unicodedata.normalize("NFKC", text).strip().lower().split())


def agents_from_label(label: str) -> list[str]:
    """Split a stored plan label such as ``docs`` or ``data+docs``."""
    return [part for part in label.split("+") if part]


__all__ = ["agents_from_label", "normalize_query"]
