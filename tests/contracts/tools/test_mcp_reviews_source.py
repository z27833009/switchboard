"""Contract tests for McpToolSource against the running reviews server (T3.08).

    uv run --env-file .env pytest tests/contracts/tools/test_mcp_reviews_source.py -q

The same adapter, token and tenant filter as the Olist source: only seller_a
allows the review tools, so the default tenant's model never sees them.
"""

from __future__ import annotations

import os
import uuid

import pytest

from switchboard.adapters.tools.mcp_client import McpToolSource
from switchboard.api.run_tokens import mint_run_token
from switchboard.core.models import RunContext, TenantContext

MCP_URL = os.environ.get("MCP_REVIEWS_URL", "").rstrip("/")
SECRET = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (MCP_URL and SECRET), reason="set MCP_REVIEWS_URL and the token secret"),
]

REVIEW_TOOLS = {"review_summary@1", "order_reviews@1"}
SELLER_TOOLS = {"orders_summary@1", "order_lookup@1", "top_products@1"} | REVIEW_TOOLS
DEFAULT_TOOLS = {
    "orders_summary@1",
    "orders_late@1",
    "order_lookup@1",
    "top_products@1",
    "describe_schema@1",
}


def mint(tenant_id: str) -> str:
    return mint_run_token(SECRET, tenant_id=tenant_id, run_id=str(uuid.uuid4()), run_timeout_s=60)


def context(tenant_id: str, allowed_tools: set[str]) -> RunContext:
    tenant = TenantContext(
        tenant_id=tenant_id,
        allowed_agents={"data"},
        allowed_tools=allowed_tools,
        provider="lmstudio",
        seller_ids=["955fee9216a65b617aa5c0531780ce60"] if tenant_id == "seller_a" else None,
    )
    return RunContext(
        run_id=str(uuid.uuid4()),
        tenant=tenant,
        run_token=mint(tenant_id),
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_seller_a_sees_the_review_tools_and_the_default_tenant_does_not() -> None:
    source = McpToolSource(name="reviews", url=MCP_URL)
    seller = await source.list_tools(context("seller_a", SELLER_TOOLS))
    assert {spec.wire_name for spec in seller} == REVIEW_TOOLS
    assert all(spec.tenant_scoped for spec in seller)
    assert all(spec.risk == "read" for spec in seller)

    default = await source.list_tools(context("default", DEFAULT_TOOLS))
    assert default == []


async def test_a_review_call_returns_a_tool_result_with_an_audit_id() -> None:
    source = McpToolSource(name="reviews", url=MCP_URL)
    result = await source.call(
        "review_summary@1",
        {"date_from": "2017-01-01", "date_to": "2018-08-31"},
        context("seller_a", SELLER_TOOLS),
    )
    assert result.total_rows > 0
    assert result.summary is not None
    assert result.summary["review_count"] == result.total_rows
    assert result.audit_id
    assert result.duration_ms >= 0
