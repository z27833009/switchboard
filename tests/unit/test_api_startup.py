"""The run token secret rule is enforced before either process serves traffic."""

import pytest

from switchboard.api.app import RUN_TOKEN_MIN_BYTES, require_run_token_secret


def test_missing_secret_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SWITCHBOARD_RUN_TOKEN_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SWITCHBOARD_RUN_TOKEN_SECRET"):
        require_run_token_secret()


def test_short_secret_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWITCHBOARD_RUN_TOKEN_SECRET", "x" * (RUN_TOKEN_MIN_BYTES - 1))
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        require_run_token_secret()


def test_long_enough_secret_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "x" * RUN_TOKEN_MIN_BYTES
    monkeypatch.setenv("SWITCHBOARD_RUN_TOKEN_SECRET", secret)
    assert require_run_token_secret() == secret
