"""Local password hashing, signed cookies and role enforcement (V3.02)."""

from __future__ import annotations

import time

from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import FakeOrchestrator, MemoryRunStore, build_harness

from switchboard.api.auth import (
    AuthUser,
    hash_password,
    sign_session,
    verify_password,
    verify_session,
)
from switchboard.api.step_events import StepEventPublisher
from switchboard.core.config import TenantsConfig


def test_password_hash_round_trip_and_rejection() -> None:
    encoded = hash_password("correct horse battery staple", iterations=1_000)
    assert encoded.startswith("pbkdf2_sha256$1000$")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong", encoded) is False
    assert verify_password("anything", None) is False
    assert verify_password("anything", "not-a-hash") is False


def test_session_round_trip_and_expiry() -> None:
    user = AuthUser(
        user_id="u1",
        email="user@example.com",
        display_name="User",
        tenant_id="default",
        role="member",
    )
    now = int(time.time())
    token = sign_session(user, "s" * 32, now=now)
    assert verify_session(token, "s" * 32) == user
    assert verify_session(token, "x" * 32) is None
    expired = sign_session(user, "s" * 32, ttl_s=1, now=now - 10)
    assert verify_session(expired, "s" * 32) is None


async def test_auth_required_rejects_anonymous_and_enforces_admin(
    tenants: TenantsConfig,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer_provider: TracerProvider,
    orchestrator: FakeOrchestrator,
) -> None:
    row = await store.create_user(
        tenant_id="default",
        email="member@example.com",
        display_name="Member",
        role="member",
    )
    await store.set_user_password(
        user_id=row["id"], password_hash=hash_password("member-password", iterations=1_000)
    )
    harness = build_harness(
        tenants=tenants,
        store=store,
        publisher=publisher,
        provider=tracer_provider,
        orchestrator=orchestrator,
        auth_required=True,
    )
    try:
        anonymous = await harness.client.get("/sessions", params={"tenant_id": "default"})
        assert anonymous.status_code == 401
        health = await harness.client.get("/health")
        assert health.status_code == 200

        bad = await harness.client.post(
            "/auth/login",
            json={
                "email": "member@example.com",
                "password": "wrong",
                "tenant_id": "default",
            },
        )
        assert bad.status_code == 401
        logged_in = await harness.client.post(
            "/auth/login",
            json={
                "email": "member@example.com",
                "password": "member-password",
                "tenant_id": "default",
            },
        )
        assert logged_in.status_code == 200, logged_in.text
        assert logged_in.json()["user"]["role"] == "member"

        allowed = await harness.client.get("/sessions", params={"tenant_id": "default"})
        assert allowed.status_code == 200
        forbidden = await harness.client.post(
            "/users",
            json={
                "tenant_id": "default",
                "email": "other@example.com",
                "display_name": "Other",
            },
        )
        assert forbidden.status_code == 403

        status = await harness.client.get("/auth/status")
        assert status.json()["authenticated"] is True
        logout = await harness.client.post("/auth/logout")
        assert logout.json()["authenticated"] is False
        after = await harness.client.get("/sessions", params={"tenant_id": "default"})
        assert after.status_code == 401
    finally:
        await harness.client.aclose()


async def test_bootstrap_password_sets_the_first_hash(
    tenants: TenantsConfig,
    store: MemoryRunStore,
    publisher: StepEventPublisher,
    tracer_provider: TracerProvider,
    orchestrator: FakeOrchestrator,
) -> None:
    await store.create_user(
        tenant_id="default",
        email="admin@local.invalid",
        display_name="Local administrator",
        role="admin",
    )
    harness = build_harness(
        tenants=tenants,
        store=store,
        publisher=publisher,
        provider=tracer_provider,
        orchestrator=orchestrator,
        auth_required=True,
        admin_password="bootstrap-password",
    )
    try:
        login = await harness.client.post(
            "/auth/login",
            json={
                "email": "admin@local.invalid",
                "password": "bootstrap-password",
                "tenant_id": "default",
            },
        )
        assert login.status_code == 200, login.text
        assert login.json()["user"]["role"] == "admin"
        stored = next(iter(store.users.values()))
        assert stored["password_hash"].startswith("pbkdf2_sha256$")
    finally:
        await harness.client.aclose()
