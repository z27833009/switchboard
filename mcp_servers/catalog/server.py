"""Catalog MCP server (V3.08).

A standalone, read-only non-Olist connector. It verifies the signed run token,
allows only the tenant named by CATALOG_TENANT, and serves one tool from the
committed catalog JSON. Run with ``python -m mcp_servers.catalog.server``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp_servers.olist.auth import TokenError, bearer_token, verify_token
from mcp_servers.olist.settings import load_manifest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = load_manifest(Path(__file__).with_name("manifest.yaml"))
DATA = ROOT / "data" / "catalog" / "products.json"
WIRE_NAME = "catalog_search@1"
ALLOWED_TENANT = os.environ.get("CATALOG_TENANT", "acme")

mcp = MCPServer(
    "catalog",
    version="1.0.0",
    instructions="Read-only local product catalog. One tool: catalog_search@1.",
)


@mcp.tool(
    name=WIRE_NAME,
    description=MANIFEST[WIRE_NAME].description,
    meta={
        "switchboard": {
            "version": MANIFEST[WIRE_NAME].version,
            "risk": MANIFEST[WIRE_NAME].risk,
            "tenant_scoped": MANIFEST[WIRE_NAME].tenant_scoped,
            "sensitive_args": list(MANIFEST[WIRE_NAME].sensitive_args),
        }
    },
)
async def catalog_search(
    ctx: Context[Any, Any],
    query: str = Field(min_length=1, max_length=120),
    limit: int = Field(default=10, ge=1, le=20),
) -> dict[str, Any]:
    try:
        headers = ctx.headers or {}
    except ValueError:
        headers = {}
    secret = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
    try:
        claims = verify_token(bearer_token(headers), secret)
    except TokenError as exc:
        raise ToolError(f"unauthorized: {exc}") from exc
    if claims.tenant_id != ALLOWED_TENANT:
        raise ToolError(f"tenant {claims.tenant_id!r} may not use this connector")
    products = json.loads(DATA.read_text(encoding="utf-8"))
    needle = query.casefold()
    rows = [
        item
        for item in products
        if needle in str(item.get("name", "")).casefold()
        or needle in str(item.get("category", "")).casefold()
    ][:limit]
    return {
        "total_rows": len(rows),
        "summary": {"query": query, "returned": len(rows)},
        "rows": rows,
    }


@mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
async def health(_: Request) -> Response:
    return JSONResponse(
        {"status": "ok", "service": "mcp-catalog", "tools": len(MANIFEST)},
        status_code=200,
    )


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_CATALOG_PORT", "8003")),
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
