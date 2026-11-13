"""Deterministic wire-to-provider tool name mapping (design.md section 8.1).

A tool wire name keeps its ``@major`` suffix (design.md section 7.2), and some
APIs reject that character: DeepSeek rejects anything outside
``^[a-zA-Z0-9_-]+$`` and the Anthropic Messages API requires tool names of
letters, digits, ``_`` and ``-``. The mapping is deterministic for a given set
of names, so an assistant tool-call message in a later turn gets the same name
as the tool definition in the first turn, and the model's answer is mapped back
to the wire name before it reaches an agent, the audit row or a span.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

INVALID_TOOL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
# Anthropic's documented tool name pattern. Used when a provider does not
# declare its own; the pattern is applied with ``fullmatch``.
ANTHROPIC_TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"


def safe_tool_names(names: Sequence[str], pattern: str | None) -> dict[str, str]:
    """Map every wire name to a name the provider accepts.

    ``pattern`` null means the identity mapping, so an OpenAI-compatible
    provider that accepts ``@`` keeps sending the wire name unchanged.
    """
    if pattern is None:
        return {name: name for name in names}
    accepted = re.compile(pattern)
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for name in sorted(set(names)):
        if accepted.fullmatch(name) and name not in used:
            safe = name
        else:
            base = name if accepted.fullmatch(name) else INVALID_TOOL_NAME_CHARS.sub("_", name)
            safe = base or "tool"
            suffix = 2
            while safe in used:
                safe = f"{base or 'tool'}_{suffix}"
                suffix += 1
        used.add(safe)
        mapping[name] = safe
    return mapping


__all__ = ["ANTHROPIC_TOOL_NAME_PATTERN", "INVALID_TOOL_NAME_CHARS", "safe_tool_names"]
