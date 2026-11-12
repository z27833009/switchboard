"""MCP client adapter for a tool server (design.md sections 7.3 and 15).

Every call carries the signed run token and the W3C ``traceparent`` header. The
server remains the trust boundary; this adapter filters the tool list for the
tenant and maps server errors to the core taxonomy.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, cast

from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool
from opentelemetry import trace
from opentelemetry.trace import SpanKind

from switchboard.core.config import ToolSourceConfig
from switchboard.core.errors import (
    ConfigError,
    RunTokenError,
    ToolExecutionError,
    ToolPermissionError,
    ToolValidationError,
)
from switchboard.core.guardrails import guardrail_span_attributes, scan
from switchboard.core.models import RunContext, ToolResult, ToolSpec
from switchboard.tracing.spans import (
    args_hash,
    inject_traceparent,
    record_error,
    set_attributes,
    tool_span_name,
)

Risk = Literal["read", "write"]


def version_of(wire_name: str) -> str:
    _, _, version = wire_name.partition("@")
    return version or "1"


def to_tool_spec(tool: McpTool) -> ToolSpec:
    meta = tool.meta if isinstance(tool.meta, dict) else {}
    switchboard = meta.get("switchboard", {})
    if not isinstance(switchboard, dict):
        switchboard = {}
    risk_value = switchboard.get("risk", "read")
    risk: Risk = "write" if risk_value == "write" else "read"
    name, _, version = tool.name.partition("@")
    return ToolSpec(
        name=name,
        version=version or str(switchboard.get("version", "1")),
        description=tool.description or "",
        input_schema=dict(tool.input_schema),
        risk=risk,
        tenant_scoped=bool(switchboard.get("tenant_scoped", True)),
        sensitive_args=[str(item) for item in switchboard.get("sensitive_args", [])],
    )


def error_text(result: CallToolResult) -> str:
    return " ".join(item.text for item in result.content if isinstance(item, TextContent))


def map_tool_error(text: str) -> Exception:
    lower = text.lower()
    if "authentication" in lower or "run token" in lower:
        return RunTokenError(text)
    if "permission denied" in lower or "not allowed" in lower:
        return ToolPermissionError(text)
    if "validation" in lower or "invalid" in lower:
        return ToolValidationError(text)
    return ToolExecutionError(text)


def parse_payload(result: CallToolResult) -> dict[str, Any]:
    structured = result.structured_content
    if isinstance(structured, dict):
        return structured
    text = error_text(result)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolExecutionError("MCP tool result is not JSON") from exc
    if not isinstance(payload, dict):
        raise ToolExecutionError("MCP tool result must be a JSON object")
    return cast(dict[str, Any], payload)


class McpToolSource:
    name: str

    def __init__(
        self,
        *,
        name: str = "olist",
        url: str,
        timeout_s: float = 30.0,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self.name = name
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s
        self._tracer = tracer or trace.get_tracer("switchboard.tools.mcp_client")

    @classmethod
    def from_config(cls, config: ToolSourceConfig) -> McpToolSource:
        if not config.url:
            raise ConfigError(f"tool source {config.name!r} needs a url")
        return cls(name=config.name, url=config.url)

    @property
    def endpoint(self) -> str:
        return self._url if self._url.endswith("/mcp") else self._url + "/mcp"

    def headers(self, ctx: RunContext) -> dict[str, str]:
        # The current span is the tool span created in call(); the server
        # continues that trace. Without an active span (list_tools, tests) the
        # static value from the run context keeps the old contract.
        return {
            "Authorization": f"Bearer {ctx.run_token}",
            "traceparent": inject_traceparent() or ctx.trace_parent,
        }

    @asynccontextmanager
    async def _session(self, ctx: RunContext) -> AsyncIterator[ClientSession]:
        async with (
            create_mcp_http_client(headers=self.headers(ctx)) as http_client,
            streamable_http_client(self.endpoint, http_client=http_client) as (read, write),
            ClientSession(read, write, read_timeout_seconds=self._timeout_s) as session,
        ):
            await session.initialize()
            yield session

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        async with self._session(ctx) as session:
            result = await session.list_tools()
        specs = [to_tool_spec(tool) for tool in result.tools]
        return [spec for spec in specs if spec.wire_name in ctx.tenant.allowed_tools]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        with self._tracer.start_as_current_span(
            tool_span_name(name),
            kind=SpanKind.CLIENT,
            record_exception=False,
            set_status_on_exception=False,
            attributes={
                "gen_ai.tool.name": name,
                "switchboard.tool.version": version_of(name),
                "switchboard.tool.args_hash": args_hash(args),
                "switchboard.run_id": ctx.run_id,
                "switchboard.tenant_id": ctx.tenant.tenant_id,
            },
        ) as span:
            try:
                result = await self._call(name, args, ctx)
            except Exception as exc:
                status = (
                    "denied" if isinstance(exc, RunTokenError | ToolPermissionError) else "error"
                )
                set_attributes(span, {"switchboard.tool.status": status})
                record_error(span, exc)
                raise
            set_attributes(
                span,
                {
                    "switchboard.tool.status": "ok",
                    "switchboard.tool.total_rows": result.total_rows,
                    "switchboard.tool.audit_id": result.audit_id,
                },
            )
            # Rows and summary are untrusted content. The span gets pattern ids
            # and counts only; the full text is never copied to telemetry.
            verdict = scan(result.model_dump_json())
            set_attributes(span, guardrail_span_attributes(verdict))
            return result

    async def _call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        started = time.perf_counter()
        try:
            async with self._session(ctx) as session:
                result = await session.call_tool(name, args)
        except Exception as exc:
            raise ToolExecutionError(f"MCP call failed: {type(exc).__name__}") from exc
        duration_ms = int((time.perf_counter() - started) * 1000)

        if result.is_error:
            raise map_tool_error(error_text(result))

        payload = parse_payload(result)
        total_rows = payload.get("total_rows")
        if not isinstance(total_rows, int):
            raise ToolExecutionError("MCP tool result has no integer total_rows")
        return ToolResult(
            tool=name,
            version=version_of(name),
            total_rows=total_rows,
            summary=payload.get("summary"),
            rows=list(payload.get("rows", [])),
            audit_id=payload.get("audit_id"),
            duration_ms=duration_ms,
        )
