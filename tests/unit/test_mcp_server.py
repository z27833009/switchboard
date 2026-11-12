"""Manifest, tenant config, argument sanitising and tool registration."""

from pathlib import Path

from mcp_servers.olist.db import hash_args, redact_args
from mcp_servers.olist.server import MANIFEST, mcp, sanitise_arguments
from mcp_servers.olist.settings import load_manifest, load_tenants

REPO = Path(__file__).resolve().parents[2]
MANIFEST_FILE = REPO / "mcp_servers" / "olist" / "manifest.yaml"
TENANTS_FILE = REPO / "config" / "tenants.yaml"


def test_manifest_has_five_versioned_read_only_tools() -> None:
    assert set(MANIFEST) == {
        "orders_summary@1",
        "orders_late@1",
        "order_lookup@1",
        "top_products@1",
        "describe_schema@1",
    }
    assert all(tool.input_schema["additionalProperties"] is False for tool in MANIFEST.values())
    assert all(tool.risk == "read" for tool in MANIFEST.values())


def test_tenants_carry_allowlists_and_seller_scope() -> None:
    tenants = load_tenants(TENANTS_FILE)
    assert tenants["default"].seller_ids is None
    assert len(tenants["default"].allowed_tools) == 5
    assert tenants["seller_a"].allowed_tools == {
        "orders_summary@1",
        "order_lookup@1",
    }
    assert tenants["seller_a"].seller_ids == ("955fee9216a65b617aa5c0531780ce60",)


def test_manifest_file_reloads_identically() -> None:
    assert load_manifest(MANIFEST_FILE) == MANIFEST


async def test_server_registers_exactly_the_manifest_tools() -> None:
    tools = await mcp.list_tools()
    assert {tool.name for tool in tools} == set(MANIFEST)
    assert len(tools) == 5


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
