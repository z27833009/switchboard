"""One live run must be visible in Jaeger (T2.13, design.md section 12.2).

    docker compose --profile tracing up -d
    $env:SWITCHBOARD_TRACING_EXPORTER="otlp"
    docker compose --profile tracing up -d
    uv run --env-file .env pytest tests/integration/test_jaeger_trace.py -q -s

The request goes through the running API and MCP server, both switched to the
OTLP exporter by the tracing profile. The Jaeger HTTP API is then queried by
``switchboard.run_id`` and the run, supervisor, agent, chat and tool spans must
all be present, including the server tool span exported by the MCP process.

Skipped when Jaeger is not running, so it costs nothing in CI.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest

JAEGER_URL = os.environ.get("JAEGER_URL", "http://localhost:16686").rstrip("/")
API_URL = os.environ.get("SWITCHBOARD_API_URL", "http://localhost:8000").rstrip("/")
MODEL = os.environ.get("LMSTUDIO_MODEL", "qwen3-8b")
QUERY = "How many orders were delivered late in March 2018?"

pytestmark = [pytest.mark.integration]


def jaeger_get(path: str, **params: Any) -> Any:
    response = httpx.get(f"{JAEGER_URL}{path}", params=params, timeout=5.0)
    response.raise_for_status()
    return response.json()


def jaeger_is_running() -> bool:
    try:
        jaeger_get("/api/services")
    except Exception:
        return False
    return True


def find_trace(run_id: str, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Query by the run id tag, retrying while Jaeger indexes the new trace."""
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        payload = jaeger_get(
            "/api/traces",
            service="switchboard-api",
            tags=json.dumps({"switchboard.run_id": run_id}),
            lookback="1h",
            limit=20,
        )
        traces = payload.get("data") or []
        if traces:
            return dict(traces[0])
        last = payload
        time.sleep(0.5)
    raise AssertionError(f"no Jaeger trace for run {run_id}: {last}")


def span_names(trace: dict[str, Any]) -> list[str]:
    return [str(span.get("operationName")) for span in trace.get("spans") or []]


def span_kinds(trace: dict[str, Any]) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {}
    for span in trace.get("spans") or []:
        tags = {tag["key"]: tag.get("value") for tag in span.get("tags") or []}
        kinds.setdefault(str(span.get("operationName")), set()).add(str(tags.get("span.kind")))
    return kinds


def test_one_run_is_visible_in_jaeger() -> None:
    if not jaeger_is_running():
        pytest.skip(f"Jaeger is not running at {JAEGER_URL}; start the tracing profile")

    try:
        response = httpx.post(
            f"{API_URL}/chat",
            json={"query": QUERY, "tenant_id": "default"},
            timeout=180.0,
        )
    except Exception as exc:
        pytest.skip(f"the API is not running at {API_URL}: {type(exc).__name__}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok", body
    run_id = body["run_id"]

    trace = find_trace(run_id)
    names = span_names(trace)
    kinds = span_kinds(trace)
    trace_id = trace.get("traceID")
    print(f"jaeger trace {trace_id} for run {run_id}: {sorted(names)}")

    assert trace_id
    assert "run" in names, names
    assert "supervisor.plan" in names, names
    assert any(name.startswith("agent.") for name in names), names
    assert f"chat {MODEL}" in names, names
    tool_names = [name for name in names if name.startswith("execute_tool ")]
    assert tool_names, names
    # The MCP process exports its own span over OTLP and continues the run's
    # trace from the traceparent header, so a server tool span proves that the
    # setting is honored by both processes.
    assert any("server" in kinds[name] for name in tool_names), kinds
    assert any("client" in kinds[name] for name in tool_names), kinds
