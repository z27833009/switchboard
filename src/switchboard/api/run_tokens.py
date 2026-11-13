"""Run token minting (design.md sections 7.3 and 13.4, ADR 0014).

The API mints one token per run and ``McpToolSource`` sends it to the tool
server, which is the trust boundary. The lifetime is the configured run timeout
plus a 30 second margin, so a run that finishes at the timeout still has a valid
token for the final tool call and the audit write.
"""

from __future__ import annotations

import math
import os
import time
import uuid
from collections.abc import Mapping

import jwt

ISSUER = "switchboard-api"
# Platform-wide audience (ADR 0014, T3.08): one run token is valid at every MCP
# server, so the API mints one token per run instead of one per tool source.
AUDIENCE = "switchboard-mcp"
ALGORITHM = "HS256"
MIN_SECRET_BYTES = 32
TOKEN_TTL_MARGIN_S = 30
RUN_TOKEN_MIN_BYTES = MIN_SECRET_BYTES


def require_run_token_secret(env: Mapping[str, str] | None = None) -> str:
    """Return the signing secret, or refuse to start (design 13.2)."""
    source = os.environ if env is None else env
    secret = source.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise RuntimeError(
            f"SWITCHBOARD_RUN_TOKEN_SECRET must be set to at least {MIN_SECRET_BYTES} bytes"
        )
    return secret


def token_lifetime_s(run_timeout_s: float) -> int:
    """Run timeout plus the margin, rounded up so a fractional timeout is covered."""
    if run_timeout_s <= 0:
        raise ValueError("run_timeout_s must be positive")
    return math.ceil(run_timeout_s) + TOKEN_TTL_MARGIN_S


def mint_run_token(
    secret: str,
    *,
    tenant_id: str,
    run_id: str,
    run_timeout_s: float,
    issued_at: int | None = None,
) -> str:
    """Mint the HS256 run token described in ADR 0014.

    ``tenant_id`` and ``run_id`` are the only source of identity for the tool
    server; the token is never logged and never returned to the client.
    """
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise ValueError(f"the run token secret must be at least {MIN_SECRET_BYTES} bytes")
    if not tenant_id:
        raise ValueError("tenant_id is required")
    uuid.UUID(run_id)
    now = int(time.time()) if issued_at is None else issued_at
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "iat": now,
        "exp": now + token_lifetime_s(run_timeout_s),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


__all__ = [
    "ALGORITHM",
    "AUDIENCE",
    "ISSUER",
    "MIN_SECRET_BYTES",
    "RUN_TOKEN_MIN_BYTES",
    "TOKEN_TTL_MARGIN_S",
    "mint_run_token",
    "require_run_token_secret",
    "token_lifetime_s",
]
