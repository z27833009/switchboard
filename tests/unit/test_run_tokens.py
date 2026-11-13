"""Run token minting matches what the tool server verifies (ADR 0014)."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest

from switchboard.api.run_tokens import (
    AUDIENCE,
    ISSUER,
    MIN_SECRET_BYTES,
    TOKEN_TTL_MARGIN_S,
    mint_run_token,
    require_run_token_secret,
    token_lifetime_s,
)

SECRET = "k" * MIN_SECRET_BYTES


def test_claims_are_the_ones_the_server_requires() -> None:
    run_id = str(uuid.uuid4())
    token = mint_run_token(SECRET, tenant_id="seller_a", run_id=run_id, run_timeout_s=60)
    claims = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "iss", "aud", "tenant_id", "run_id", "jti"]},
    )
    assert claims["iss"] == "switchboard-api"
    assert claims["aud"] == AUDIENCE
    assert claims["tenant_id"] == "seller_a"
    assert claims["run_id"] == run_id
    assert claims["exp"] - claims["iat"] == 60 + TOKEN_TTL_MARGIN_S
    assert uuid.UUID(claims["jti"])
    assert claims["iat"] <= int(time.time()) + 1


def test_lifetime_is_the_run_timeout_plus_thirty_seconds_rounded_up() -> None:
    assert token_lifetime_s(60) == 90
    assert token_lifetime_s(60.4) == 91
    assert token_lifetime_s(0.5) == 31
    with pytest.raises(ValueError):
        token_lifetime_s(0)


def test_short_secret_and_bad_run_id_are_refused() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        mint_run_token("short", tenant_id="default", run_id=str(uuid.uuid4()), run_timeout_s=60)
    with pytest.raises(ValueError):
        mint_run_token(SECRET, tenant_id="default", run_id="not-a-uuid", run_timeout_s=60)


def test_token_is_bound_to_the_audience() -> None:
    token = mint_run_token(SECRET, tenant_id="default", run_id=str(uuid.uuid4()), run_timeout_s=60)
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, SECRET, algorithms=["HS256"], audience="mcp-other")


def test_secret_rule_reads_an_explicit_environment() -> None:
    with pytest.raises(RuntimeError, match="SWITCHBOARD_RUN_TOKEN_SECRET"):
        require_run_token_secret({})
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        require_run_token_secret({"SWITCHBOARD_RUN_TOKEN_SECRET": "x" * 31})
    assert require_run_token_secret({"SWITCHBOARD_RUN_TOKEN_SECRET": SECRET}) == SECRET
