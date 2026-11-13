"""Manifest, tenant config, argument sanitising and tool registration."""

from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_servers.olist import server
from mcp_servers.olist.db import hash_args, redact_args
from mcp_servers.olist.server import MANIFEST, mcp, sanitise_arguments
from mcp_servers.olist.settings import ServerConfigError, load_manifest, load_tenants
from mcp_servers.olist.sql_guard import MAX_SQL_LENGTH

REPO = Path(__file__).resolve().parents[2]
MANIFEST_FILE = REPO / "mcp_servers" / "olist" / "manifest.yaml"
TENANTS_FILE = REPO / "config" / "tenants.yaml"


def test_manifest_has_six_versioned_read_only_tools() -> None:
    assert set(MANIFEST) == {
        "orders_summary@1",
        "orders_late@1",
        "order_lookup@1",
        "top_products@1",
        "describe_schema@1",
        "query_readonly_sql@1",
    }
    assert all(tool.input_schema["additionalProperties"] is False for tool in MANIFEST.values())
    assert all(tool.risk == "read" for tool in MANIFEST.values())


def test_the_sql_tool_is_tenant_scoped_and_bounded() -> None:
    tool = MANIFEST["query_readonly_sql@1"]
    assert tool.tenant_scoped is True
    sql = tool.input_schema["properties"]["sql"]
    assert sql["type"] == "string"
    assert sql["maxLength"] == MAX_SQL_LENGTH
    assert "disabled by default" in tool.description.lower()


def test_tenants_carry_allowlists_and_seller_scope() -> None:
    tenants = load_tenants(TENANTS_FILE, MANIFEST)
    assert tenants["default"].seller_ids is None
    assert len(tenants["default"].allowed_tools) == 5
    assert tenants["seller_a"].allowed_tools == {
        "orders_summary@1",
        "order_lookup@1",
        "top_products@1",
    }
    # The review tools in the same allowlist belong to mcp_servers/reviews and
    # are ignored by this server.
    assert "review_summary@1" not in tenants["seller_a"].allowed_tools
    assert "order_reviews@1" not in tenants["seller_a"].allowed_tools
    assert tenants["seller_a"].ask_tools == frozenset()
    assert tenants["seller_a"].seller_ids == ("955fee9216a65b617aa5c0531780ce60",)


def test_no_demo_tenant_allows_the_sql_tool() -> None:
    tenants = load_tenants(TENANTS_FILE, MANIFEST)
    for tenant_id, tenant in tenants.items():
        assert "query_readonly_sql@1" not in tenant.allowed_tools, tenant_id
        assert "query_readonly_sql@1" not in tenant.ask_tools, tenant_id


def write_tenants(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tenants.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_foreign_and_malformed_tool_wire_names(tmp_path: Path) -> None:
    """A foreign name is ignored here; a malformed name still fails.

    The tenants file is shared with mcp_servers/reviews, so this server only
    enforces the names it serves. The API checks the union of every manifest
    declared in platform.yaml (tests/unit/test_config.py).
    """
    foreign = write_tenants(
        tmp_path,
        """
tenants:
  default:
    provider: lmstudio
    agents:
      allow: ["data"]
    tools:
      allow: ["ghost_tool@1"]
""",
    )
    tenants = load_tenants(foreign, MANIFEST)
    assert tenants["default"].allowed_tools == frozenset()

    malformed = write_tenants(
        tmp_path,
        """
tenants:
  default:
    tools:
      allow: ["ghost_tool"]
""",
    )
    with pytest.raises(ServerConfigError, match=r"malformed wire names.*ghost_tool"):
        load_tenants(malformed, MANIFEST)


def test_unknown_keys_duplicates_and_ask_overlap_fail(tmp_path: Path) -> None:
    typo = write_tenants(
        tmp_path,
        """
tenants:
  default:
    provider: lmstudio
    toolz:
      allow: []
""",
    )
    with pytest.raises(ServerConfigError, match=r"unknown keys.*toolz"):
        load_tenants(typo, MANIFEST)

    duplicate = write_tenants(
        tmp_path,
        """
tenants:
  default:
    tools:
      allow: ["orders_late@1", "orders_late@1"]
""",
    )
    with pytest.raises(ServerConfigError, match="duplicates"):
        load_tenants(duplicate, MANIFEST)

    overlap = write_tenants(
        tmp_path,
        """
tenants:
  default:
    tools:
      allow: ["orders_late@1"]
      ask: ["orders_late@1"]
""",
    )
    with pytest.raises(ServerConfigError, match="allowed automatically"):
        load_tenants(overlap, MANIFEST)


def test_seller_ids_and_retrieval_keys_are_validated(tmp_path: Path) -> None:
    empty = write_tenants(
        tmp_path,
        """
tenants:
  default:
    seller_ids: []
""",
    )
    with pytest.raises(ServerConfigError, match="non-empty list"):
        load_tenants(empty, MANIFEST)

    non_string = write_tenants(
        tmp_path,
        """
tenants:
  default:
    seller_ids: [123]
""",
    )
    with pytest.raises(ServerConfigError, match="entries must be strings"):
        load_tenants(non_string, MANIFEST)

    retrieval = write_tenants(
        tmp_path,
        """
tenants:
  default:
    retrieval:
      kk: 5
""",
    )
    with pytest.raises(ServerConfigError, match="retrieval has unknown keys"):
        load_tenants(retrieval, MANIFEST)


def test_manifest_file_reloads_identically() -> None:
    assert load_manifest(MANIFEST_FILE) == MANIFEST


async def test_server_registers_exactly_the_manifest_tools() -> None:
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == set(MANIFEST)
    assert len(tools) == 6


async def test_query_tool_refuses_sql_before_any_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard runs first: a refused statement never opens a connection."""

    async def forbidden(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("dispatch must not run for a refused statement")

    monkeypatch.setattr(server, "_call_tool", forbidden)
    with pytest.raises(ToolError, match="only SELECT statements are allowed"):
        await server.query_readonly_sql(cast(Any, object()), "DELETE FROM olist.orders")


def test_seller_ids_may_not_contain_commas(tmp_path: Path) -> None:
    path = write_tenants(
        tmp_path,
        """
tenants:
  default:
    seller_ids: ["seller-1,seller-2"]
""",
    )
    with pytest.raises(ServerConfigError, match="must not contain commas"):
        load_tenants(path, MANIFEST)


def test_identity_arguments_are_stripped() -> None:
    clean = sanitise_arguments(
        {
            "date_from": "2018-03-01",
            "tenant_id": "seller_a",
            "seller_ids": ["x"],
            "run_token": "forged",
        }
    )
    assert clean == {"date_from": "2018-03-01"}


def test_audit_hash_and_redaction() -> None:
    args = {"order_id": "abc", "secret": "value"}
    assert hash_args(args) == hash_args({"secret": "value", "order_id": "abc"})
    assert redact_args(args, ["secret"]) == {
        "order_id": "abc",
        "secret": "[redacted]",
    }
