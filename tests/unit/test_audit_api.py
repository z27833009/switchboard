"""``GET /audit`` with a memory store: filters, pagination and redaction.

No database is involved; the store implements the same tenant filter and
ordering as ``LIST_AUDIT_SQL`` so the route, the query validation and the
response shape are tested without the compose stack (AC-18a, AC-18b).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.fakes import AppHarness, build_harness

from switchboard.api.audit import load_sensitive_args
from switchboard.core.config import TenantsConfig, ToolSourceConfig

BASE = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def audit_row(
    *,
    tenant_id: str = "default",
    run_id: str | None = None,
    tool_name: str = "orders_summary",
    tool_version: str = "1",
    status: str = "ok",
    duration_ms: int = 7,
    created_at: datetime = BASE,
    args: dict[str, Any] | None = None,
    args_hash: str = "a" * 64,
) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "tenant_id": tenant_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "args_hash": args_hash,
        "args_redacted": dict(args or {"date_from": "2018-03-01"}),
        "status": status,
        "duration_ms": duration_ms,
        "created_at": created_at,
    }


async def test_audit_query_validation(harness: AppHarness) -> None:
    missing_tenant = await harness.client.get("/audit")
    assert missing_tenant.status_code == 422
    empty_tenant = await harness.client.get("/audit", params={"tenant_id": ""})
    assert empty_tenant.status_code == 422

    unknown_tenant = await harness.client.get("/audit", params={"tenant_id": "acme"})
    assert unknown_tenant.status_code == 404
    assert "unknown tenant" in unknown_tenant.json()["detail"]

    for limit in (0, 201):
        invalid = await harness.client.get(
            "/audit", params={"tenant_id": "default", "limit": limit}
        )
        assert invalid.status_code == 422, (limit, invalid.text)
    negative_offset = await harness.client.get(
        "/audit", params={"tenant_id": "default", "offset": -1}
    )
    assert negative_offset.status_code == 422

    bad_status = await harness.client.get(
        "/audit", params={"tenant_id": "default", "status": "maybe"}
    )
    assert bad_status.status_code == 422
    bad_run = await harness.client.get(
        "/audit", params={"tenant_id": "default", "run_id": "not-a-uuid"}
    )
    assert bad_run.status_code == 422
    reversed_range = await harness.client.get(
        "/audit",
        params={
            "tenant_id": "default",
            "from": "2026-11-12T12:00:00Z",
            "to": "2026-11-12T10:00:00Z",
        },
    )
    assert reversed_range.status_code == 422
    assert reversed_range.json()["detail"] == "from must be on or before to"

    max_limit = await harness.client.get("/audit", params={"tenant_id": "default", "limit": 200})
    assert max_limit.status_code == 200


async def test_audit_lists_newest_first_and_filters(harness: AppHarness) -> None:
    run_a = str(uuid.uuid4())
    run_b = str(uuid.uuid4())
    older = audit_row(run_id=run_a, created_at=BASE)
    newer = audit_row(
        run_id=run_b,
        tool_name="orders_late",
        status="denied",
        duration_ms=3,
        created_at=BASE.replace(hour=11),
        args={"date_from": "2018-01-01", "date_to": "2018-01-31"},
    )
    other_tenant = audit_row(
        tenant_id="seller_a",
        run_id=str(uuid.uuid4()),
        created_at=BASE.replace(hour=12),
    )
    harness.store.audit_rows.extend([newer, older, other_tenant])

    listed = await harness.client.get("/audit", params={"tenant_id": "default"})
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert [row["id"] for row in body["rows"]] == [newer["id"], older["id"]]
    assert set(body["rows"][0]) == {
        "id",
        "run_id",
        "tenant_id",
        "tool_name",
        "tool_version",
        "args_hash",
        "args_redacted",
        "status",
        "duration_ms",
        "created_at",
    }
    assert body["rows"][0]["run_id"] == run_b
    assert body["rows"][0]["tool_version"] == "1"
    assert body["rows"][0]["args_redacted"] == {
        "date_from": "2018-01-01",
        "date_to": "2018-01-31",
    }

    by_tool = await harness.client.get(
        "/audit", params={"tenant_id": "default", "tool_name": "orders_late"}
    )
    assert [row["id"] for row in by_tool.json()["rows"]] == [newer["id"]]
    by_status = await harness.client.get(
        "/audit", params={"tenant_id": "default", "status": "denied"}
    )
    assert [row["id"] for row in by_status.json()["rows"]] == [newer["id"]]
    by_run = await harness.client.get("/audit", params={"tenant_id": "default", "run_id": run_a})
    assert [row["id"] for row in by_run.json()["rows"]] == [older["id"]]

    after = await harness.client.get(
        "/audit", params={"tenant_id": "default", "from": "2026-11-12T10:30:00Z"}
    )
    assert [row["id"] for row in after.json()["rows"]] == [newer["id"]]
    before = await harness.client.get(
        "/audit", params={"tenant_id": "default", "to": "2026-11-12T10:30:00Z"}
    )
    assert [row["id"] for row in before.json()["rows"]] == [older["id"]]
    between = await harness.client.get(
        "/audit",
        params={
            "tenant_id": "default",
            "from": "2026-11-12T10:00:00Z",
            "to": "2026-11-12T11:00:00Z",
        },
    )
    assert {row["id"] for row in between.json()["rows"]} == {older["id"], newer["id"]}

    seller = await harness.client.get("/audit", params={"tenant_id": "seller_a"})
    assert [row["id"] for row in seller.json()["rows"]] == [other_tenant["id"]]


async def test_audit_paginates_with_limit_and_offset(harness: AppHarness) -> None:
    rows = [
        audit_row(created_at=BASE.replace(minute=index), tool_name=f"tool_{index}")
        for index in range(5)
    ]
    harness.store.audit_rows.extend(rows)
    expected = [row["id"] for row in reversed(rows)]

    pages: list[list[str]] = []
    for offset in (0, 2, 4):
        response = await harness.client.get(
            "/audit", params={"tenant_id": "default", "limit": 2, "offset": offset}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["limit"] == 2
        assert body["offset"] == offset
        assert body["count"] == (1 if offset == 4 else 2)
        pages.append([row["id"] for row in body["rows"]])

    assert pages[0] == expected[:2]
    assert pages[1] == expected[2:4]
    assert pages[2] == expected[4:]
    combined = [row_id for page in pages for row_id in page]
    assert sorted(combined) == sorted(expected)
    beyond = await harness.client.get("/audit", params={"tenant_id": "default", "offset": 10})
    assert beyond.json()["count"] == 0


async def test_audit_redacts_sensitive_args_from_the_manifest(
    harness: AppHarness, tenants: TenantsConfig
) -> None:
    """A raw sensitive value in the row can never reach the response (AC-18b)."""
    row = audit_row(
        tool_name="order_lookup",
        args={"order_id": "synthetic-secret", "note": "kept"},
        args_hash="b" * 64,
    )
    harness.store.audit_rows.append(row)
    guarded = build_harness(
        tenants=tenants,
        store=harness.store,
        publisher=harness.publisher,
        provider=harness.provider,
        orchestrator=harness.orchestrator,
        sensitive_args={"order_lookup@1": ["order_id"]},
    )
    try:
        response = await guarded.client.get("/audit", params={"tenant_id": "default"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["count"] == 1
        returned = body["rows"][0]
        assert returned["args_redacted"] == {"order_id": "[redacted]", "note": "kept"}
        assert returned["args_hash"] == "b" * 64
        assert "synthetic-secret" not in response.text
    finally:
        await guarded.client.aclose()


def test_load_sensitive_args_reads_every_declared_manifest(tmp_path: Path) -> None:
    """The read guard is driven by the manifest, which the server also obeys."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
tools:
  - name: lookup
    version: "1"
    description: Look up one record.
    risk: read
    sensitive_args: [secret, token]
    input_schema:
      type: object
""",
        encoding="utf-8",
    )
    sources = [
        ToolSourceConfig(name="tools", adapter="mcp", manifest=str(manifest)),
        ToolSourceConfig(name="plain", adapter="mcp"),
    ]
    assert load_sensitive_args(sources) == {"lookup@1": ("secret", "token")}
    assert load_sensitive_args([]) == {}
