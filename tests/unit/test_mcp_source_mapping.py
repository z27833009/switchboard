"""Mapping from MCP tool metadata to core ToolSpec and error taxonomy."""

import json
import uuid

import pytest
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as McpTool

from switchboard.adapters.tools.mcp_client import (
    McpToolSource,
    map_tool_error,
    parse_payload,
    to_tool_spec,
    version_of,
)
from switchboard.core.config import ToolSourceConfig
from switchboard.core.errors import (
    ConfigError,
    RunTokenError,
    ToolExecutionError,
    ToolPermissionError,
    ToolValidationError,
)
from switchboard.core.models import RunContext, TenantContext
from switchboard.registry import TOOL_SOURCES, Registry


def tool_payload() -> dict[str, object]:
    return {
        "name": "orders_summary@1",
        "description": "Count orders.",
        "inputSchema": {"type": "object", "properties": {}},
        "_meta": {
            "switchboard": {
                "version": "1",
                "risk": "read",
                "tenant_scoped": True,
                "sensitive_args": ["order_id"],
            }
        },
    }


def test_version_is_read_from_the_wire_name() -> None:
    assert version_of("orders_late@1") == "1"
    assert version_of("orders_late") == "1"


def test_metadata_becomes_a_tool_spec() -> None:
    spec = to_tool_spec(McpTool.model_validate(tool_payload()))
    assert spec.name == "orders_summary"
    assert spec.version == "1"
    assert spec.wire_name == "orders_summary@1"
    assert spec.risk == "read"
    assert spec.tenant_scoped is True
    assert spec.sensitive_args == ["order_id"]


def test_missing_metadata_falls_back_to_read_and_tenant_scoped() -> None:
    payload = tool_payload()
    del payload["_meta"]
    spec = to_tool_spec(McpTool.model_validate(payload))
    assert spec.risk == "read"
    assert spec.tenant_scoped is True
    assert spec.sensitive_args == []


def test_error_text_maps_to_the_core_taxonomy() -> None:
    assert isinstance(map_tool_error("authentication failed: token expired"), RunTokenError)
    assert isinstance(map_tool_error("permission denied: not allowed"), ToolPermissionError)
    assert isinstance(map_tool_error("invalid arguments"), ToolValidationError)
    assert isinstance(map_tool_error("tool execution failed"), ToolExecutionError)


def test_payload_is_parsed_from_text_content() -> None:
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(
                    {"summary": {"orders": 1}, "rows": [], "total_rows": 1, "audit_id": "a"}
                ),
            )
        ]
    )
    assert parse_payload(result)["total_rows"] == 1


def test_non_json_payload_is_an_execution_error() -> None:
    result = CallToolResult(content=[TextContent(type="text", text="not json")])
    with pytest.raises(ToolExecutionError):
        parse_payload(result)


def tenant() -> TenantContext:
    return TenantContext(
        tenant_id="default",
        allowed_agents={"data"},
        allowed_tools={"orders_summary@1"},
        provider="lmstudio",
    )


def test_headers_carry_the_run_token_and_traceparent() -> None:
    source = McpToolSource(url="http://mcp.test:8001")
    ctx = RunContext(
        run_id=str(uuid.uuid4()),
        tenant=tenant(),
        run_token="signed-token",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )
    headers = source.headers(ctx)
    assert headers["Authorization"] == "Bearer signed-token"
    assert headers["traceparent"] == ctx.trace_parent


def test_endpoint_defaults_to_the_mcp_path() -> None:
    assert McpToolSource(url="http://mcp.test:8001").endpoint == "http://mcp.test:8001/mcp"
    assert McpToolSource(url="http://mcp.test:8001/mcp/").endpoint == "http://mcp.test:8001/mcp"


def test_from_config_requires_a_url() -> None:
    with pytest.raises(ConfigError, match="url"):
        McpToolSource.from_config(ToolSourceConfig(name="olist", adapter="mcp"))


def test_registry_resolves_the_tool_source_entry_point() -> None:
    assert Registry().load(TOOL_SOURCES, "mcp") is McpToolSource
