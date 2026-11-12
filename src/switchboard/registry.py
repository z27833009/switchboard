"""Registry over Python entry points (design.md section 16).

Agents, providers, orchestrators, tool sources, retrievers, embedders, caches
and eval suites are registered, never hard-coded. A third-party package can add
one by declaring the same entry point group, with no change in this repository.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Iterable
from typing import Any, Protocol

from switchboard.core.errors import RegistryError, UnknownComponentError

ORCHESTRATORS = "switchboard.orchestrators"
PROVIDERS = "switchboard.providers"
AGENTS = "switchboard.agents"
TOOL_SOURCES = "switchboard.tool_sources"
RETRIEVERS = "switchboard.retrievers"
EMBEDDERS = "switchboard.embedders"
CACHES = "switchboard.caches"
EVAL_SUITES = "switchboard.eval_suites"


class EntryPointLike(Protocol):
    name: str
    group: str

    def load(self) -> Any: ...


class Registry:
    """Resolve components by entry point group and name.

    ``entries`` exists for tests and for embedding a fixed set; production code
    omits it and reads the installed distribution's entry points.
    """

    def __init__(self, entries: Iterable[EntryPointLike] | None = None) -> None:
        selected = list(entries) if entries is not None else list(importlib.metadata.entry_points())
        self._entries: dict[str, dict[str, EntryPointLike]] = {}
        for entry in selected:
            group = self._entries.setdefault(entry.group, {})
            if entry.name in group:
                raise RegistryError(
                    f"duplicate entry point {entry.name!r} in group {entry.group!r}"
                )
            group[entry.name] = entry

    def names(self, group: str) -> list[str]:
        return sorted(self._entries.get(group, {}))

    def load(self, group: str, name: str) -> Any:
        entries = self._entries.get(group, {})
        try:
            entry = entries[name]
        except KeyError as exc:
            raise UnknownComponentError(
                f"no {group!r} entry point named {name!r}; "
                f"available: {', '.join(sorted(entries)) or 'none'}"
            ) from exc
        return entry.load()

    def load_all(self, group: str) -> dict[str, Any]:
        return {name: self.load(group, name) for name in self.names(group)}

    def agents(self) -> dict[str, Any]:
        return self.load_all(AGENTS)
