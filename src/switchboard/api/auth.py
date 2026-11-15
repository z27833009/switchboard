"""Local password hashing and signed session cookies (V3.02).

No external identity provider and no new dependency: PBKDF2-HMAC-SHA256 comes
from the standard library, and a session token is a base64url JSON payload plus
an HMAC-SHA256 signature. The cookie is HttpOnly and SameSite=Lax; a deployment
that terminates TLS should also set Secure (the API does when the request is
HTTPS).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

SESSION_COOKIE = "switchboard_session"
PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 210_000
SESSION_TTL_S = 12 * 60 * 60


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    display_name: str
    tenant_id: str
    role: str


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            PASSWORD_SCHEME,
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    except (ValueError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def sign_session(
    user: AuthUser, secret: str, *, ttl_s: int = SESSION_TTL_S, now: int | None = None
) -> str:
    issued = int(time.time()) if now is None else now
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "name": user.display_name,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "exp": issued + ttl_s,
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(
        hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{body}.{signature}"


def verify_session(token: str, secret: str) -> AuthUser | None:
    try:
        body, signature = token.split(".", 1)
        expected = _b64(
            hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload: dict[str, Any] = json.loads(_unb64(body))
        if int(payload["exp"]) < int(time.time()):
            return None
        return AuthUser(
            user_id=str(payload["sub"]),
            email=str(payload["email"]),
            display_name=str(payload["name"]),
            tenant_id=str(payload["tenant_id"]),
            role=str(payload["role"]),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, UnicodeError):
        return None


__all__ = [
    "PASSWORD_ITERATIONS",
    "SESSION_COOKIE",
    "SESSION_TTL_S",
    "AuthUser",
    "hash_password",
    "sign_session",
    "verify_password",
    "verify_session",
]
