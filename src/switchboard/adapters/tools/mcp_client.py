"""MCP client adapter for a tool server (design.md sections 7.3 and 15).

Every call carries the signed run token and the W3C ``traceparent`` header. The
server remains the trust boundary; this adapter filters the tool list for the
tenant and maps server errors to the core taxonomy.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from mcp import ClientSession
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)
from mcp.types import CallToolResult, TextContent, ToolListChangedNotification
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
from switchboard.core.tool_pinning import tool_fingerprint
from switchboard.tracing.spans import (
    args_hash,
    inject_traceparent,
    record_error,
    set_attributes,
    tool_span_name,
)

Risk = Literal["read", "write"]

logger = logging.getLogger(__name__)


@dataclass
class _RunSession:
    """One MCP session shared by every tool call in a run and source (V3.11).

    The transport is opened and closed inside a dedicated task. MCP's streamable
    HTTP transport uses an anyio cancel scope, and an anyio scope must be exited
    in the task that entered it; tool calls therefore send commands to this task
    instead of holding the session in the caller's task.
    """

    commands: asyncio.Queue[tuple[str, str, dict[str, Any], str, asyncio.Future[Any]]] = field(
        default_factory=asyncio.Queue
    )
    task: asyncio.Task[None] | None = None
    tools: list[ToolSpec] | None = None
    # Set per command so the auth hook can read the caller's active tool span.
    traceparent: str = ""


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


async def discover_tools(url: str, *, timeout_s: float = 10.0) -> list[ToolSpec]:
    """Read a server's tool list without a run token (V3.03).

    The MCP servers expose ``tools/list`` as a public catalog: the run token is
    required to call a tool, not to describe it. The discovered specs are stored
    inline on the source so a later restart can validate tenant allowlists
    without a manifest file.
    """
    clean = url.strip()
    if not clean.startswith(("http://", "https://")):
        raise ConfigError("MCP server URL must start with http:// or https://")
    endpoint = clean if clean.endswith("/mcp") else clean.rstrip("/") + "/mcp"
    try:
        async with (
            create_mcp_http_client(headers={}) as http_client,
            streamable_http_client(endpoint, http_client=http_client) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timeout_s) as session,
        ):
            await session.initialize()
            result = await session.list_tools()
    except Exception as exc:
        raise ConfigError(f"could not list tools at {clean}: {type(exc).__name__}: {exc}") from exc
    return [to_tool_spec(tool) for tool in result.tools]


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


class PinnedToolSource:
    """Hide or deny a tool whose current fingerprint is not approved (V3.10)."""

    def __init__(self, inner: McpToolSource, approved: Mapping[str, str]) -> None:
        self._inner = inner
        self._approved = dict(approved)
        self.name = inner.name

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        specs = await self._inner.list_tools(ctx)
        return [
            spec for spec in specs if self._approved.get(spec.wire_name) == tool_fingerprint(spec)
        ]

    async def call(self, name: str, args: dict[str, Any], ctx: RunContext) -> ToolResult:
        specs = await self._inner.list_tools(ctx)
        current = next((spec for spec in specs if spec.wire_name == name), None)
        approved = self._approved.get(name)
        if current is None or approved is None or approved != tool_fingerprint(current):
            raise ToolPermissionError(f"tool {name!r} is not approved")
        return await self._inner.call(name, args, ctx)

    async def close_run(self, run_id: str) -> None:
        """Forward the run cleanup to the transport, if it owns one (V3.11)."""
        close = getattr(self._inner, "close_run", None)
        if close is not None:
            await close(run_id)


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
        self._sessions: dict[str, _RunSession] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

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

    def _auth(self, ctx: RunContext, state: _RunSession) -> Any:
        """Set the run token and the command's traceparent on every request.

        The session lives across several tool calls, so its headers cannot be
        fixed at initialize time: each ``tools/call`` must continue the span
        that is active in the caller's task, even though the request itself is
        sent from the session task (V3.11, V3.13).
        """

        def apply(request: Any) -> Any:
            request.headers["Authorization"] = f"Bearer {ctx.run_token}"
            request.headers["traceparent"] = state.traceparent or ctx.trace_parent
            return request

        return apply

    async def _serve(
        self,
        ctx: RunContext,
        state: _RunSession,
        ready: asyncio.Future[None],
    ) -> None:
        """Own the transport and execute one command at a time (V3.11)."""

        async def on_message(message: Any) -> None:
            if isinstance(message, ToolListChangedNotification):
                # Drop the cached list. The next tools/list asks the server
                # again and PinnedToolSource re-evaluates every fingerprint.
                state.tools = None

        try:
            async with (
                create_mcp_http_client(auth=self._auth(ctx, state)) as http_client,
                streamable_http_client(self.endpoint, http_client=http_client) as (read, write),
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=self._timeout_s,
                    message_handler=on_message,
                ) as session,
            ):
                with self._tracer.start_as_current_span(
                    "mcp.initialize",
                    kind=SpanKind.CLIENT,
                    attributes={
                        "mcp.server.name": self.name,
                        "switchboard.run_id": ctx.run_id,
                        "switchboard.tenant_id": ctx.tenant.tenant_id,
                    },
                ):
                    await session.initialize()
                ready.set_result(None)
                while True:
                    kind, name, args, traceparent, future = await state.commands.get()
                    state.traceparent = traceparent
                    if kind == "close":
                        future.set_result(None)
                        return
                    try:
                        if kind == "list":
                            if state.tools is None:
                                result = await session.list_tools()
                                state.tools = [to_tool_spec(tool) for tool in result.tools]
                            future.set_result(state.tools)
                        elif kind == "call":
                            future.set_result(await session.call_tool(name, args))
                        else:  # pragma: no cover - internal
                            raise RuntimeError(f"unknown MCP session command {kind!r}")
                    except Exception as exc:
                        if not future.done():
                            future.set_exception(exc)
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            while True:
                try:
                    _, _, _, _, future = state.commands.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if not future.done():
                    future.set_exception(exc)
            if not isinstance(exc, asyncio.CancelledError):
                logger.warning("MCP session for run %s ended: %s", ctx.run_id, exc)

    async def _open_session(self, ctx: RunContext) -> _RunSession:
        state = _RunSession()
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        state.task = asyncio.create_task(self._serve(ctx, state, ready))
        self._sessions[ctx.run_id] = state
        try:
            await ready
        except BaseException:
            self._sessions.pop(ctx.run_id, None)
            state.task.cancel()
            with contextlib.suppress(BaseException):
                await state.task
            raise
        return state

    async def _state(self, ctx: RunContext) -> _RunSession:
        lock = self._session_locks.get(ctx.run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[ctx.run_id] = lock
        async with lock:
            state = self._sessions.get(ctx.run_id)
            if state is None:
                state = await self._open_session(ctx)
            return state

    async def close_run(self, run_id: str) -> None:
        """Ask the session task to close, in the task that opened the transport."""
        state = self._sessions.pop(run_id, None)
        self._session_locks.pop(run_id, None)
        if state is None or state.task is None or state.task.done():
            return
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await state.commands.put(("close", "", {}, "", future))
        try:
            await asyncio.wait_for(future, timeout=10.0)
        except Exception as exc:  # cleanup must never change the run result
            logger.warning("closing MCP session for run %s failed: %s", run_id, exc)
        with contextlib.suppress(BaseException):
            await asyncio.wait_for(state.task, timeout=10.0)

    async def list_tools(self, ctx: RunContext) -> list[ToolSpec]:
        state = await self._state(ctx)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await state.commands.put(("list", "", {}, inject_traceparent() or ctx.trace_parent, future))
        specs: list[ToolSpec] = await future
        # An ask tool is listed too, so the LangGraph path can offer it and pause
        # for approval before calling it. The agent loop drops every write and
        # ask spec again, so a tenant's ask list never widens the read-only path
        # (design.md sections 6.7, 6.9 and 7.3).
        permitted = ctx.tenant.allowed_tools | ctx.tenant.ask_tools
        return [spec for spec in specs if spec.wire_name in permitted]

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
        state = await self._state(ctx)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await state.commands.put(
            ("call", name, args, inject_traceparent() or ctx.trace_parent, future)
        )
        try:
            result = await future
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
