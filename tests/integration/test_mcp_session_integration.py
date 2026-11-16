"""V3.11 integration: one MCP initialize for three calls in one run.

Skipped unless the compose stack and ``.env`` are present. The test uses the
real Olist MCP server and an in-memory span exporter, so it does not need the
PostgreSQL span table.
"""

from __future__ import annotations

import os
import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.api.run_tokens import mint_run_token
from switchboard.core.models import RunContext, TenantContext

MCP_URL = os.environ.get("MCP_OLIST_URL", "").rstrip("/")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (MCP_URL and SECRET),
        reason="set MCP_OLIST_URL and the run token secret",
    ),
]


def context(run_id: str) -> RunContext:
    return RunContext(
        run_id=run_id,
        tenant=TenantContext(
            tenant_id="default",
            allowed_agents={"data"},
            allowed_tools={"orders_summary@1"},
            provider="lmstudio",
        ),
        run_token=mint_run_token(SECRET, tenant_id="default", run_id=run_id, run_timeout_s=60.0),
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_three_calls_share_one_initialize() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    source = McpToolSource(
        name="olist",
        url=MCP_URL,
        tracer=provider.get_tracer("switchboard.test.mcp_session"),
    )
    ctx = context(str(uuid.uuid4()))
    try:
        specs = await source.list_tools(ctx)
        assert "orders_summary@1" in [spec.wire_name for spec in specs]
        for _ in range(3):
            result = await source.call(
                "orders_summary@1",
                {"date_from": "2017-01-01", "date_to": "2017-12-31"},
                ctx,
            )
            assert result.total_rows >= 0
    finally:
        await source.close_run(ctx.run_id)

    names = [span.name for span in exporter.get_finished_spans()]
    assert names.count("mcp.initialize") == 1
    assert names.count("execute_tool orders_summary@1") == 3
