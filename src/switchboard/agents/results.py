"""Run-scoped tool result storage and the result_page@1 platform tool."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from switchboard.core.errors import ToolExecutionError, ToolValidationError
from switchboard.core.models import RunContext, ToolResult, ToolResultView, ToolSpec


@dataclass
class ResultStore:
    _results: dict[str, ToolResult] = field(default_factory=dict)

    def add(self, result: ToolResult) -> str:
        result_id = result.result_id
        if not result_id or result_id in self._results:
            result_id = f"r_{uuid.uuid4().hex[:8]}"
        self._results[result_id] = result.model_copy(update={"result_id": result_id})
        return result_id

    def get(self, result_id: str) -> ToolResult:
        try:
            return self._results[result_id]
        except KeyError as exc:
            raise ToolValidationError(f"unknown result_id {result_id!r}") from exc


def result_page_spec() -> ToolSpec:
    return ToolSpec(
        name="result_page",
        version="1",
        description=(
            "Return further rows of a tool result already fetched in this run. "
            "Use offset 0 for the first page."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "result_id": {"type": "string", "minLength": 1},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["result_id", "offset", "limit"],
        },
        risk="read",
        tenant_scoped=False,
    )


class ResultPageToolSource:
    """In-process platform tool source for result_page@1."""

    name = "platform"

    def __init__(self, store: ResultStore) -> None:
        self._store = store
        self._spec = result_page_spec()

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        return [self._spec]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        if name != "result_page@1":
            raise ToolExecutionError(f"unknown platform tool {name!r}")
        result_id = args.get("result_id")
        offset = args.get("offset", 0)
        limit = args.get("limit", 20)
        if not isinstance(result_id, str) or not result_id:
            raise ToolValidationError("result_id must be a non-empty string")
        if not isinstance(offset, int) or offset < 0:
            raise ToolValidationError("offset must be a non-negative integer")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ToolValidationError("limit must be between 1 and 50")
        original = self._store.get(result_id)
        rows = original.rows[offset : offset + limit]
        return ToolResult(
            tool="result_page@1",
            version="1",
            total_rows=original.total_rows,
            summary={
                "offset": offset,
                "limit": limit,
                "source_result_id": result_id,
            },
            rows=rows,
            result_id=result_id,
            audit_id=original.audit_id,
        )


def tool_view(result_id: str, result: ToolResult, limit: int) -> ToolResultView:
    return result.view(limit=limit, result_id=result_id)


def citation_snippet(result: ToolResult, view: ToolResultView) -> str:
    summary = json.dumps(view.summary or {}, ensure_ascii=False)[:200]
    return f"{view.total_rows} rows, preview {len(view.rows_preview)}; {summary}"
