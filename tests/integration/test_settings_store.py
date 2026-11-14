from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import asyncpg
import pytest

from switchboard.config_store import SettingsStore

REPO = Path(__file__).resolve().parents[2]
DATABASE_URL = os.environ.get("SWITCHBOARD_DATABASE_URL", "")
CONFIG_ENV = {
    "MCP_OLIST_URL": "http://localhost:8001",
    "MCP_REVIEWS_URL": "http://localhost:8002",
    "LMSTUDIO_BASE_URL": "http://localhost:18080/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set SWITCHBOARD_DATABASE_URL",
    ),
]


@pytest.mark.asyncio
async def test_settings_store_reads_and_versions_the_live_rows() -> None:
    connection = await asyncpg.connect(DATABASE_URL)
    try:
        async with connection.transaction():
            store = SettingsStore(connection, REPO / "config", CONFIG_ENV)
            loaded = await store.load()
            assert loaded.tenants.tenants

            raw_platform = (
                await connection.fetchrow(
                    "SELECT payload FROM public.settings_documents WHERE kind = 'platform'"
                )
            )["payload"]
            platform = (
                json.loads(raw_platform) if isinstance(raw_platform, str) else dict(raw_platform)
            )
            revision = int(
                await connection.fetchval(
                    "SELECT revision FROM public.settings_documents WHERE kind = 'platform'"
                )
            )
            changed = dict(platform)
            changed["limits"] = dict(platform["limits"])
            changed["limits"]["max_plan_size"] = 2
            actor = f"integration:test:{uuid.uuid4()}"
            new_revision = await store.replace_document(
                "platform", changed, actor=actor, note="rollback me"
            )
            assert new_revision == revision + 1

            exported = changed
            assert exported["limits"]["max_plan_size"] == 2
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM public.settings_revisions WHERE actor = $1",
                    actor,
                )
                == 1
            )
    finally:
        await connection.close()
