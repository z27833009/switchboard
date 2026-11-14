from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from switchboard.config_store import SettingsStore
from switchboard.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[2]
CONFIG_ENV = {
    "MCP_OLIST_URL": "http://localhost:8001",
    "MCP_REVIEWS_URL": "http://localhost:8002",
    "LMSTUDIO_BASE_URL": "http://localhost:18080/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
}


class FakeDB:
    """The small asyncpg surface SettingsStore uses, with real revision checks."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.revisions: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def fetchval(self, query: str, *args: Any) -> Any:
        if "count(*)" in query:
            return len(self.documents)
        if "INSERT INTO public.settings_documents" in query:
            kind, payload, actor = args
            if kind in self.documents:
                return None
            self.documents[kind] = {
                "payload": json.loads(payload),
                "revision": 1,
                "updated_by": actor,
            }
            return 1
        if "UPDATE public.settings_documents" in query:
            kind, payload, actor = args
            if kind not in self.documents:
                return None
            self.documents[kind] = {
                "payload": json.loads(payload),
                "revision": self.documents[kind]["revision"] + 1,
                "updated_by": actor,
            }
            return self.documents[kind]["revision"]
        raise AssertionError(f"unexpected fetchval: {query}")

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM public.settings_documents" in query:
            return [
                {"kind": kind, "payload": entry["payload"]}
                for kind, entry in self.documents.items()
            ]
        raise AssertionError(f"unexpected fetch: {query}")

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        if "FROM public.settings_documents WHERE kind = $1" in query:
            entry = self.documents.get(args[0])
            return None if entry is None else {"payload": entry["payload"]}
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        if "INSERT INTO public.settings_revisions" in query:
            kind, revision, payload, actor, note = args
            self.revisions.append(
                {
                    "kind": kind,
                    "revision": revision,
                    "payload": json.loads(payload),
                    "actor": actor,
                    "note": note,
                }
            )
            return "INSERT 0 1"
        if "INSERT INTO public.config_audit" in query:
            kind, revision, action, actor, detail = args
            self.audits.append(
                {
                    "kind": kind,
                    "revision": revision,
                    "action": action,
                    "actor": actor,
                    "detail": json.loads(detail),
                }
            )
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


@pytest.mark.asyncio
async def test_seed_load_and_export_round_trip(tmp_path: Path) -> None:
    db = FakeDB()
    store = SettingsStore(db, REPO / "config", CONFIG_ENV)

    assert await store.seed_if_empty() is True
    assert await store.seed_if_empty() is False
    settings = await store.load()
    assert settings.platform.orchestrator
    assert set(db.documents) == {"platform", "providers", "tenants", "prices"}
    assert {revision.get("action", "seed") for revision in db.revisions} == {"seed"}
    assert [audit["action"] for audit in db.audits] == ["seed"] * 4

    paths = await store.export_yaml(tmp_path)
    assert {path.name for path in paths} == {
        "platform.yaml",
        "providers.yaml",
        "tenants.yaml",
        "prices.yaml",
    }
    exported = yaml.safe_load((tmp_path / "platform.yaml").read_text(encoding="utf-8"))
    assert exported["orchestrator"] == settings.platform.orchestrator


@pytest.mark.asyncio
async def test_replace_writes_a_revision_and_rejects_invalid_payload() -> None:
    db = FakeDB()
    store = SettingsStore(db, REPO / "config", CONFIG_ENV)
    await store.seed_if_empty()

    platform = db.documents["platform"]["payload"]
    changed = dict(platform)
    changed["limits"] = dict(platform["limits"])
    changed["limits"]["max_plan_size"] = 2
    revision = await store.replace_document(
        "platform", changed, actor="test:editor", note="max plan size"
    )
    assert revision == 2
    assert db.revisions[-1]["actor"] == "test:editor"
    assert db.audits[-1]["action"] == "replace"
    assert db.documents["platform"]["payload"]["limits"]["max_plan_size"] == 2

    broken = dict(changed)
    broken["typo_field"] = True
    with pytest.raises(ConfigError):
        await store.replace_document("platform", broken, actor="test:editor")
    assert db.documents["platform"]["payload"]["limits"]["max_plan_size"] == 2
    assert len(db.revisions) == 5


@pytest.mark.asyncio
async def test_load_refuses_a_partial_control_plane() -> None:
    db = FakeDB()
    db.documents["platform"] = {"payload": {"orchestrator": "langgraph"}, "revision": 1}
    store = SettingsStore(db, REPO / "config", CONFIG_ENV)
    with pytest.raises(ConfigError, match="missing"):
        await store.load()


@pytest.mark.asyncio
async def test_add_tool_source_appends_and_validates() -> None:
    db = FakeDB()
    store = SettingsStore(db, REPO / "config", CONFIG_ENV)
    await store.seed_if_empty()
    manifest = REPO / "mcp_servers" / "olist" / "manifest.yaml"
    revision = await store.add_tool_source(
        name="crm",
        url="http://crm:9000/mcp",
        manifest=str(manifest),
        actor="test:admin",
    )
    assert revision == 2
    sources = db.documents["platform"]["payload"]["tool_sources"]
    assert sources[-1] == {
        "name": "crm",
        "adapter": "mcp",
        "url": "http://crm:9000/mcp",
        "manifest": str(manifest),
    }

    with pytest.raises(ConfigError, match="already exists"):
        await store.add_tool_source(
            name="crm",
            url="http://crm:9000/mcp",
            manifest=str(manifest),
            actor="test:admin",
        )
    with pytest.raises(ConfigError, match="manifest not found"):
        await store.add_tool_source(
            name="missing",
            url="http://missing:9000/mcp",
            manifest="does-not-exist.yaml",
            actor="test:admin",
        )
    with pytest.raises(ConfigError, match="http"):
        await store.add_tool_source(
            name="bad-url",
            url="ftp://bad",
            manifest=str(manifest),
            actor="test:admin",
        )


@pytest.mark.asyncio
async def test_update_tenant_and_provider_documents() -> None:
    db = FakeDB()
    store = SettingsStore(db, REPO / "config", CONFIG_ENV)
    await store.seed_if_empty()

    revision = await store.update_tenant(
        "seller_a",
        agents_allow=["data"],
        tools_allow=["orders_summary@1"],
        tools_ask=[],
        actor="test:admin",
    )
    assert revision == 2
    seller = db.documents["tenants"]["payload"]["tenants"]["seller_a"]
    assert seller["agents"]["allow"] == ["data"]
    assert seller["tools"]["allow"] == ["orders_summary@1"]

    with pytest.raises(ConfigError, match="unknown agents"):
        await store.update_tenant("seller_a", agents_allow=["ghost"], actor="test:admin")
    with pytest.raises(ConfigError, match="unknown tenant"):
        await store.update_tenant("ghost", agents_allow=["data"], actor="test:admin")

    revision = await store.update_provider_model("lmstudio", "qwen3-8b-next", actor="test:admin")
    assert revision == 2
    assert db.documents["providers"]["payload"]["providers"]["lmstudio"]["model"] == "qwen3-8b-next"
    with pytest.raises(ConfigError, match="unknown provider"):
        await store.update_provider_model("ghost", "model", actor="test:admin")
