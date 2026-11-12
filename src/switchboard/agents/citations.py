"""Citation markers and parsing (design 6.4)."""

from __future__ import annotations

import re

from switchboard.core.models import Citation

MARKER = re.compile(r"\[([dt]\d+)\]")


class CitationRegistry:
    def __init__(self) -> None:
        self._by_ref: dict[str, Citation] = {}

    def add(self, citation: Citation) -> None:
        self._by_ref[citation.ref] = citation

    def parse(self, answer: str) -> list[Citation]:
        refs = dict.fromkeys(MARKER.findall(answer))
        return [self._by_ref[ref] for ref in refs if ref in self._by_ref]
