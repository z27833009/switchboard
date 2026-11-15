"""A small in-process read-only catalog connector (V3.08).

It is deliberately not an MCP server: the second connector acceptance needs a
source that is not Olist and does not require a second process. The tool contract
still comes from a manifest, so the API validates tenant allowlists exactly as it
does for MCP sources.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from switchboard.core.config import ToolSourceConfig
from switchboard.core.errors import ToolPermissionError, ToolValidationError
from switchboard.core.models import RunContext, ToolResult, ToolSpec

WIRE_NAME = "catalog_search@1"
DEFAULT_DATA = Path("data/catalog/products.json")


class CatalogToolSource:
    name = "catalog"

    def __init__(self, manifest: Path, data: Path) -> None:
        self._manifest = manifest
        self._data = data
        self._spec = self._load_spec(manifest)

    @classmethod
    def from_config(cls, source: ToolSourceConfig) -> CatalogToolSource:
        if source.manifest is None:
            raise ValueError("the catalog source requires a manifest")
        data = Path(source.url) if source.url else DEFAULT_DATA
        return cls(Path(source.manifest), data)

    @staticmethod
    def _load_spec(path: Path) -> ToolSpec:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or len(entries) != 1:
            raise ValueError(f"{path} must declare exactly one catalog tool")
        return ToolSpec.model_validate(entries[0])

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        permitted = ctx.tenant.allowed_tools | ctx.tenant.ask_tools
        return [self._spec] if self._spec.wire_name in permitted else []

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        if name != WIRE_NAME:
            raise ToolPermissionError(f"{name} is not served by the catalog source")
        if WIRE_NAME not in ctx.tenant.allowed_tools:
            raise ToolPermissionError(f"{name} is not allowed for this tenant")
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolValidationError("query must be a non-empty string")
        limit = args.get("limit", 10)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ToolValidationError("limit must be an integer between 1 and 20")
        products = json.loads(self._data.read_text(encoding="utf-8"))
        needle = query.casefold()
        rows = [
            item
            for item in products
            if needle in str(item.get("name", "")).casefold()
            or needle in str(item.get("category", "")).casefold()
        ][:limit]
        return ToolResult(
            tool="catalog_search",
            version="1",
            total_rows=len(rows),
            rows=rows,
        )


__all__ = ["CatalogToolSource"]
