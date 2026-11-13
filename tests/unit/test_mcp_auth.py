"""Run token verification and bearer parsing (ADR 0014)."""

import time
import uuid

import jwt
import pytest
from mcp_servers.olist.auth import (
    TokenError,
    bearer_token,
    verify_token,
)

SECRET = "x" * 32
OTHER_SECRET = "y" * 32


def token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "switchboard-api",
        "aud": "switchboard-mcp",
        "tenant_id": "default",
        "run_id": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_token_returns_claims() -> None:
    value = token()
    claims = verify_token(value, SECRET)
    assert claims.tenant_id == "default"
    assert uuid.UUID(claims.run_id)
    assert claims.token_id


def test_bearer_header_is_parsed_case_insensitively() -> None:
    assert bearer_token({"Authorization": "Bearer abc"}) == "abc"
    assert bearer_token({"authorization": "bearer abc"}) == "abc"


def test_missing_and_malformed_headers_are_rejected() -> None:
    with pytest.raises(TokenError, match="missing bearer"):
        bearer_token({})
    with pytest.raises(TokenError, match="missing bearer"):
        bearer_token({"Authorization": "Basic abc"})
    with pytest.raises(TokenError, match="empty bearer"):
        bearer_token({"Authorization": "Bearer "})


def test_expired_token_is_rejected() -> None:
    expired = token(exp=int(time.time()) - 10)
    with pytest.raises(TokenError, match="expired"):
        verify_token(expired, SECRET)


def test_forged_token_is_rejected() -> None:
    forged = jwt.encode(
        {
            "iss": "switchboard-api",
            "aud": "switchboard-mcp",
            "tenant_id": "default",
            "run_id": str(uuid.uuid4()),
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "jti": str(uuid.uuid4()),
        },
        OTHER_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(TokenError, match="invalid run token"):
        verify_token(forged, SECRET)


def test_wrong_audience_and_missing_claims_are_rejected() -> None:
    with pytest.raises(TokenError, match="invalid run token"):
        verify_token(token(aud="other"), SECRET)
    with pytest.raises(TokenError, match="invalid run token"):
        verify_token(token(tenant_id=None), SECRET)


def test_run_id_must_be_a_uuid() -> None:
    with pytest.raises(TokenError, match="UUID"):
        verify_token(token(run_id="not-a-uuid"), SECRET)


def test_short_secret_is_rejected() -> None:
    with pytest.raises(TokenError, match="32 bytes"):
        verify_token(token(), "short")
