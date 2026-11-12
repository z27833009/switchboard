"""Placeholder MCP tool server for T1.02.

It answers health checks only. T1.07 replaces it with the FastMCP server that
carries the five read-only Olist tools.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RUN_TOKEN_MIN_BYTES = 32
DEFAULT_PORT = 8001


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({"status": "ok", "service": "mcp-olist"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Keep container logs quiet until the real server has something to say.
        return


def main() -> None:
    secret = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
    if len(secret.encode("utf-8")) < RUN_TOKEN_MIN_BYTES:
        raise SystemExit("SWITCHBOARD_RUN_TOKEN_SECRET must be set to at least 32 bytes")
    port = int(os.environ.get("MCP_OLIST_PORT", str(DEFAULT_PORT)))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
