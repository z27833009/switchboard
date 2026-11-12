"""Signed run token verification (ADR 0014).

The token is the only source of tenant and run identity. Headers, tool
arguments and metadata are never trusted for identity.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import jwt

AUDIENCE = "mcp-olist"
ISSUER = "switchboard-api"
MIN_SECRET_BYTES = 32


class TokenError(Exception):
    pass


@dataclass(frozen=True)
class TokenClaims:
    tenant_id: str
    run_id: str
    token_id: str


def bearer_token(headers: Mapping[str, str]) -> str:
    value = headers.get("authorization") or headers.get("Authorization")
    if not value or not value.lower().startswith("bearer "):
        raise TokenError("missing bearer run token")
    token = value[7:].strip()
    if not token:
        raise TokenError("empty bearer run token")
    return token


def verify_token(
    token: str,
    secret: str,
    *,
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
) -> TokenClaims:
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise TokenError("run token secret is shorter than 32 bytes")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iat", "iss", "aud", "tenant_id", "run_id", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("run token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid run token: {exc}") from exc

    tenant_id = claims.get("tenant_id")
    run_id = claims.get("run_id")
    token_id = claims.get("jti")
    if not isinstance(tenant_id, str) or not tenant_id:
        raise TokenError("token is missing tenant_id")
    if not isinstance(run_id, str) or not run_id:
        raise TokenError("token is missing run_id")
    if not isinstance(token_id, str) or not token_id:
        raise TokenError("token is missing jti")
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise TokenError("run_id must be a UUID") from exc
    return TokenClaims(tenant_id=tenant_id, run_id=run_id, token_id=token_id)
