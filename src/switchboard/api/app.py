"""Placeholder API for T1.02: startup checks and a dependency health report.

The real routes (chat, runs, trace) arrive in T1.13. This module exists so that
`docker compose up` has something to start, and so the run token secret rule in
design.md section 13.2 is enforced from the first process.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

RUN_TOKEN_MIN_BYTES = 32
HTTP_TIMEOUT_S = 3.0


def require_run_token_secret() -> str:
    """Return the signing secret, or refuse to start (design 13.2)."""
    secret = os.environ.get("SWITCHBOARD_RUN_TOKEN_SECRET", "")
    if len(secret.encode("utf-8")) < RUN_TOKEN_MIN_BYTES:
        raise RuntimeError("SWITCHBOARD_RUN_TOKEN_SECRET must be set to at least 32 bytes")
    return secret


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    require_run_token_secret()
    yield


app = FastAPI(title="Switchboard API", version="0.1.0", lifespan=lifespan)


async def check_postgres(dsn: str) -> dict[str, str]:
    if not dsn:
        return {"status": "error", "detail": "SWITCHBOARD_DATABASE_URL is not set"}
    try:
        connection = await asyncpg.connect(dsn=dsn, timeout=HTTP_TIMEOUT_S)
        try:
            await connection.fetchval("SELECT 1")
        finally:
            await connection.close()
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok"}


def check_lmstudio(base_url: str) -> dict[str, Any]:
    if not base_url:
        return {"status": "error", "detail": "LMSTUDIO_BASE_URL is not set"}
    try:
        with urllib.request.urlopen(
            base_url.rstrip("/") + "/models", timeout=HTTP_TIMEOUT_S
        ) as response:
            payload: Any = json.loads(response.read())
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    models = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id")
    ]
    return {"status": "ok", "models": sorted(models)}


def check_http(url: str) -> dict[str, str]:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as response:
            response.read()
    except Exception as exc:
        return {"status": "error", "detail": type(exc).__name__}
    return {"status": "ok"}


@app.get("/health")
async def health() -> JSONResponse:
    postgres = await check_postgres(os.environ.get("SWITCHBOARD_DATABASE_URL", ""))
    lmstudio = await asyncio.to_thread(check_lmstudio, os.environ.get("LMSTUDIO_BASE_URL", ""))
    tools_url = os.environ.get("SWITCHBOARD_TOOLS_URL", "")
    if tools_url:
        mcp_olist = await asyncio.to_thread(check_http, tools_url.rstrip("/") + "/health")
    else:
        mcp_olist = {"status": "error", "detail": "SWITCHBOARD_TOOLS_URL is not set"}
    dependencies: dict[str, dict[str, Any]] = {
        "postgres": postgres,
        "mcp_olist": mcp_olist,
        "lmstudio": lmstudio,
    }
    healthy = all(item["status"] == "ok" for item in dependencies.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "error",
            "dependencies": dependencies,
        },
    )
