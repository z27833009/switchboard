"""The in-process non-Olist catalog connector (V3.08)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from switchboard.adapters.tools.catalog import CatalogToolSource
from switchboard.core.config import ToolSourceConfig
from switchboard.core.errors import ToolPermissionError
from switchboard.core.models import RunContext, TenantContext

REPO = Path(__file__).resolve().parents[2]


def context(allowed: set[str]) -> RunContext:
    return RunContext(
        run_id=str(uuid.uuid4()),
        tenant=TenantContext(
            tenant_id="acme",
            allowed_agents={"data"},
            allowed_tools=allowed,
            provider="lmstudio",
        ),
        run_token="unused",
        trace_parent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    )


async def test_catalog_search_is_scoped_and_read_only() -> None:
    source = CatalogToolSource.from_config(
        ToolSourceConfig(
            name="catalog",
            adapter="catalog",
            manifest="mcp_servers/catalog/manifest.yaml",
        )
    )
    ctx = context({"catalog_search@1"})
    specs = await source.list_tools(ctx)
    assert [spec.wire_name for spec in specs] == ["catalog_search@1"]
    assert specs[0].risk == "read"
    result = await source.call("catalog_search@1", {"query": "desk"}, ctx)
    assert result.total_rows == 2
    assert {row["sku"] for row in result.rows} == {"acme-desk", "acme-lamp"}

    denied = await source.list_tools(context(set()))
    assert denied == []
    with pytest.raises(ToolPermissionError):
        await source.call("catalog_search@1", {"query": "desk"}, context(set()))
