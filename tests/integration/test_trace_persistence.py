"""Read-after-run trace completeness against the real MCP server and database.

    uv run --env-file .env pytest tests/integration/test_trace_persistence.py -q

One run goes through the real LangGraph orchestrator with the real
``OpenAICompatibleProvider`` (a fake HTTP client replays three recorded shapes:
a plan, a tool call and a final answer) and the real ``McpToolSource`` pointing
at the running MCP server. The run row is inserted first. Immediately after
``force_flush`` the database must already hold the run, supervisor, agent, chat
and tool spans, including the tool span written by the MCP process, which is a
child of the client tool span in the same trace (AC-05d).

Skipped unless the compose stack and ``.env`` are present. CI runs it once the
PostgreSQL service exists (T1.16).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

import asyncpg
import jwt
import pytest
from openai.types.chat import ChatCompletion
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from switchboard.adapters.orchestrator.langgraph_impl import LangGraphOrchestrator
from switchboard.adapters.providers.openai_compat import STRUCTURED_TOOL_NAME
from switchboard.adapters.providers.openai_compat import (
    OpenAICompatibleProvider as Provider,
)
from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.core.config import Limits
from switchboard.core.context import ClearOldestToolResults
from switchboard.core.models import (
    ChatRequest,
    ProviderCapabilities,
    RunContext,
    TenantContext,
)
from switchboard.registry import Registry
from switchboard.tracing import (
    TOOL_RESULT_ATTRIBUTE,
    PostgresSpanExporter,
    force_flush_async,
    insert_run,
)

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
APP_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
ADMIN_URL = os.environ.get("SWITCHBOARD_DATABASE_ADMIN_URL", "")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and APP_URL and ADMIN_URL and SECRET),
        reason="set MCP_OLIST_URL and the database/secret variables",
    ),
]

ALL_TOOLS = {
    "orders_summary@1",
    "orders_late@1",
    "order_lookup@1",
    "top_products@1",
    "describe_schema@1",
}


def mint(tenant_id: str, run_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "switchboard-mcp",
            "tenant_id": tenant_id,
            "run_id": run_id,
            "iat": now,
            "exp": now + 120,
            "jti": str(uuid.uuid4()),
        },
        SECRET,
        algorithm="HS256",
    )


def completion(
    *,
    content: str | None = None,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
) -> ChatCompletion:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_name is not None:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments or {}),
                },
            }
        ]
    return ChatCompletion.model_validate(
        {
            "id": "chatcmpl-trace-test",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen3-8b",
            "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 12,
                "total_tokens": 132,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
        }
    )


class ScriptedCompletions:
    """Replays the three model calls of one data run, no network involved."""

    def __init__(self, orders: int) -> None:
        self.responses = [
            completion(
                tool_name=STRUCTURED_TOOL_NAME,
                arguments={"agents": ["data"], "reason": "orders question"},
                call_id="call-plan",
            ),
            completion(
                tool_name="orders_summary@1",
                arguments={"date_from": "2018-03-01", "date_to": "2018-03-31"},
                call_id="call-tool",
            ),
            completion(
                content=f"There were {orders} orders in March 2018 [t1].",
                call_id="call-answer",
            ),
        ]

    async def create(self, **kwargs: Any) -> ChatCompletion:
        return self.responses.pop(0)


def run_context(run_id: str) -> RunContext:
    tenant = TenantContext(
        tenant_id="default",
        allowed_agents={"data"},
        allowed_tools=ALL_TOOLS,
        provider="lmstudio",
    )
    return RunContext(
        run_id=run_id,
        tenant=tenant,
        run_token=mint("default", run_id),
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_one_run_is_readable_immediately_and_contains_both_processes() -> None:
    run_id = str(uuid.uuid4())
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(BatchSpanProcessor(PostgresSpanExporter(APP_URL)))
    tracer = tracer_provider.get_tracer("switchboard.test.trace")

    source = McpToolSource(url=MCP_URL, tracer=tracer)
    # The scripted answer must use a number the tool result actually contains;
    # the gate rejects an invented count. The pre-query uses its own run id so it
    # does not add spans or audit rows to the run under test.
    summary = await source.call(
        "orders_summary@1",
        {"date_from": "2018-03-01", "date_to": "2018-03-31"},
        run_context(str(uuid.uuid4())),
    )
    orders = (summary.summary or {}).get("orders")
    assert isinstance(orders, int), summary.summary

    client = type(
        "Client",
        (),
        {"chat": type("Chat", (), {"completions": ScriptedCompletions(orders)})()},
    )()
    provider = Provider(
        name="lmstudio",
        model="qwen3-8b",
        base_url="http://test.invalid/v1",
        api_key="test",
        capabilities=ProviderCapabilities(
            tools=True,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        ),
        client=client,
        tracer=tracer,
    )
    orchestrator = LangGraphOrchestrator(
        provider=provider,
        registry=Registry(),
        context_policy=ClearOldestToolResults(),
        limits=Limits(run_timeout_s=60.0),
        tool_sources=[source],
        tracer=tracer,
    )

    app_pool = await asyncpg.create_pool(dsn=APP_URL, min_size=1, max_size=2)
    try:
        await insert_run(
            app_pool,
            run_id=run_id,
            tenant_id="default",
            query="How many orders were placed in March 2018?",
            orchestrator="langgraph",
        )
        started_row = await app_pool.fetchrow(
            "SELECT status, started_at FROM public.runs WHERE id = $1", uuid.UUID(run_id)
        )
        assert started_row is not None
        assert started_row["status"] == "running"
        assert started_row["started_at"] is not None

        result = await orchestrator.run(
            ChatRequest(query="How many orders were placed in March 2018?", tenant_id="default"),
            run_context(run_id),
        )
        assert result.status == "ok", result.errors
        assert result.answer is not None

        # Read-after-run consistency: no sleep, no retry loop (AC-05d).
        assert await force_flush_async(2.0, provider=tracer_provider) is True
    finally:
        await app_pool.close()

    connection = await asyncpg.connect(ADMIN_URL)
    try:
        rows = await connection.fetch(
            """
            SELECT span_id, trace_id, parent_span_id, run_id, name, kind, status,
                   attributes, events
            FROM public.spans
            WHERE run_id = $1
            ORDER BY start_time
            """,
            uuid.UUID(run_id),
        )
        run_row = await connection.fetchrow(
            "SELECT status FROM public.runs WHERE id = $1", uuid.UUID(run_id)
        )
        audit = await connection.fetchrow(
            """
            SELECT tool_name, status, args_hash
            FROM public.tool_audit
            WHERE run_id = $1
            ORDER BY created_at
            """,
            uuid.UUID(run_id),
        )
    finally:
        await connection.close()

    assert run_row is not None and run_row["status"] == "running"

    names = [row["name"] for row in rows]
    assert "run" in names
    assert "supervisor.plan" in names
    assert "agent.data" in names
    assert "chat qwen3-8b" in names
    assert names.count("execute_tool orders_summary@1") == 2, names

    trace_ids = {row["trace_id"] for row in rows}
    assert len(trace_ids) == 1, trace_ids
    span_ids = {row["span_id"]: row for row in rows}
    run_span = next(row for row in rows if row["name"] == "run")
    assert run_span["parent_span_id"] is None
    assert run_span["kind"] == "server"

    tool_rows = [row for row in rows if row["name"] == "execute_tool orders_summary@1"]
    client_span = next(row for row in tool_rows if row["kind"] == "client")
    server_span = next(row for row in tool_rows if row["kind"] == "server")
    assert server_span["span_id"] in span_ids
    assert server_span["parent_span_id"] == client_span["span_id"]

    server_attributes = server_span["attributes"]
    if isinstance(server_attributes, str):
        server_attributes = json.loads(server_attributes)
    client_attributes = client_span["attributes"]
    if isinstance(client_attributes, str):
        client_attributes = json.loads(client_attributes)
    assert server_attributes["gen_ai.tool.name"] == "orders_summary@1"
    assert server_attributes["switchboard.tool.status"] == "ok"
    assert (
        server_attributes["switchboard.tool.total_rows"]
        == client_attributes["switchboard.tool.total_rows"]
    )
    assert (
        server_attributes["switchboard.tool.audit_id"]
        == client_attributes["switchboard.tool.audit_id"]
    )
    assert audit is not None
    assert audit["tool_name"] == "orders_summary"
    assert audit["status"] == "ok"

    events = server_span["events"]
    if isinstance(events, str):
        events = json.loads(events)
    result_events = [event for event in events if event["name"] == "tool.result"]
    assert len(result_events) == 1
    assert result_events[0]["attributes"]["switchboard.tool.result.truncated"] is False
    full_result = json.loads(result_events[0]["attributes"][TOOL_RESULT_ATTRIBUTE])
    assert set(full_result) >= {"summary", "rows", "total_rows", "audit_id"}
    assert len(full_result["rows"]) > 0
