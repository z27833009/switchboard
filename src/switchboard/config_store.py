"""Database-backed settings control plane (V3.01).

The four YAML files remain the bootstrap and export format. The API reads the
same four documents from ``public.settings_documents`` at startup, validates
them with the ordinary pydantic models, and seeds the table from YAML exactly
once when it is empty. Every seed or replacement appends a revision and an
audit row, so a later editable settings API has a history to build on.

This module deliberately owns the database access and the YAML I/O, not the
schema models: ``switchboard.config.settings_from_documents`` remains the one
validation path shared with the YAML loader.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from switchboard.config import read_yaml, settings_from_documents
from switchboard.core.config import Settings
from switchboard.core.errors import ConfigError

CONFIG_KINDS = ("platform", "providers", "tenants", "prices")
SEED_ACTOR = "system:yaml-seed"
SEED_NOTE = "seeded from the committed YAML files"


class SettingsStore:
    """Read and version the four settings documents in PostgreSQL.

    ``db`` is an asyncpg connection. The API opens a short-lived connection for
    startup loading and closes it before creating the long-lived pool, so this
    store does not depend on pool lifecycle.
    """

    def __init__(
        self,
        db: Any,
        config_dir: Path,
        env: Mapping[str, str] | None = None,
        *,
        actor: str = SEED_ACTOR,
    ) -> None:
        self._db = db
        self._config_dir = Path(config_dir)
        self._env = dict(os.environ) if env is None else dict(env)
        self._actor = actor

    async def load(self) -> Settings:
        """Read the documents, seeding them from YAML once if the table is empty."""
        await self.seed_if_empty()
        rows = await self._db.fetch("SELECT kind, payload FROM public.settings_documents")
        documents = {str(row["kind"]): _as_mapping(row["payload"]) for row in rows}
        missing = sorted(set(CONFIG_KINDS) - set(documents))
        if missing:
            raise ConfigError(
                "settings_documents is missing "
                f"{missing}; run `switchboard config seed --dir {self._config_dir}`"
            )
        return settings_from_documents(documents, env=self._env, label="settings_documents")

    async def seed_if_empty(self) -> bool:
        """Insert the four YAML documents if the control plane has no rows.

        Returns True when this call performed the seed. The check and inserts
        run in one transaction; the primary key makes a concurrent second
        seeder fail loudly rather than silently overwrite a row.
        """
        count = await self._db.fetchval("SELECT count(*) FROM public.settings_documents")
        if count:
            return False
        documents = self._read_yaml_documents()
        # Validate before writing anything. A broken YAML file must not leave a
        # half-seeded control plane.
        settings_from_documents(documents, env=self._env, label=f"{self._config_dir} (seed)")
        async with self._db.transaction():
            for kind in CONFIG_KINDS:
                payload = documents[kind]
                revision = await self._db.fetchval(
                    """
                    INSERT INTO public.settings_documents
                        (kind, payload, revision, updated_by)
                    VALUES ($1, $2::jsonb, 1, $3)
                    ON CONFLICT (kind) DO NOTHING
                    RETURNING revision
                    """,
                    kind,
                    _json(payload),
                    self._actor,
                )
                if revision is None:
                    raise ConfigError(f"settings_documents was seeded concurrently for {kind!r}")
                await self._append_revision(
                    kind,
                    int(revision),
                    payload,
                    SEED_NOTE,
                    actor=self._actor,
                    action="seed",
                )
        return True

    async def export_yaml(self, target_dir: Path) -> list[Path]:
        """Write the database documents back to a YAML directory.

        This is the export half of the import/export contract. It writes the
        stored payloads unchanged, including ``${ENV}`` placeholders, and does
        not touch the committed ``config/`` directory unless the caller names it.
        """
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        rows = await self._db.fetch("SELECT kind, payload FROM public.settings_documents")
        documents = {str(row["kind"]): _as_mapping(row["payload"]) for row in rows}
        missing = sorted(set(CONFIG_KINDS) - set(documents))
        if missing:
            raise ConfigError(f"cannot export, settings_documents is missing {missing}")
        paths: list[Path] = []
        for kind in CONFIG_KINDS:
            path = target / f"{kind}.yaml"
            path.write_text(
                yaml.safe_dump(documents[kind], sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            paths.append(path)
        return paths

    async def add_tool_source(
        self,
        *,
        name: str,
        url: str,
        manifest: str,
        actor: str,
        adapter: str = "mcp",
    ) -> int:
        """Append one MCP tool source to the platform document.

        A manifest is required until server-side tool discovery lands: the API
        validates tenant allowlists against manifest wire names, and a source
        without one would be invisible in the settings snapshot. The route is
        deliberately restart-to-apply: the running process keeps the tool
        sources it built at startup.
        """
        clean_name = name.strip()
        if not clean_name or not clean_name.replace("-", "").replace("_", "").isalnum():
            raise ConfigError("MCP server name must be letters, digits, '-' or '_'")
        clean_url = url.strip()
        if not clean_url.startswith(("http://", "https://")):
            raise ConfigError("MCP server URL must start with http:// or https://")
        manifest_path = Path(manifest.strip())
        if not manifest_path.is_file():
            raise ConfigError(f"MCP manifest not found: {manifest_path}")
        row = await self._db.fetchrow(
            "SELECT payload FROM public.settings_documents WHERE kind = 'platform'"
        )
        if row is None:
            raise ConfigError("platform settings document is missing")
        platform = _as_mapping(row["payload"])
        sources = list(platform.get("tool_sources", []))
        if any(
            str(source.get("name")) == clean_name for source in sources if isinstance(source, dict)
        ):
            raise ConfigError(f"MCP server {clean_name!r} already exists")
        sources.append(
            {
                "name": clean_name,
                "adapter": adapter,
                "url": clean_url,
                "manifest": str(manifest_path),
            }
        )
        platform["tool_sources"] = sources
        return await self.replace_document(
            "platform",
            platform,
            actor=actor,
            note=f"add MCP server {clean_name}",
        )

    async def replace_document(
        self,
        kind: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        note: str | None = None,
    ) -> int:
        """Replace one document after validating the complete settings set.

        This is the small write seam V3.03 will call from the settings API. It
        is not exposed over HTTP in this task. A validation failure rolls back
        before the document, revision or audit row is written.
        """
        if kind not in CONFIG_KINDS:
            raise ConfigError(f"unknown settings document {kind!r}; expected {CONFIG_KINDS}")
        rows = await self._db.fetch("SELECT kind, payload FROM public.settings_documents")
        documents = {str(row["kind"]): _as_mapping(row["payload"]) for row in rows}
        if set(documents) != set(CONFIG_KINDS):
            raise ConfigError("cannot replace a document before the control plane is seeded")
        documents[kind] = dict(payload)
        settings_from_documents(documents, env=self._env, label=f"settings_documents/{kind}")
        async with self._db.transaction():
            revision = await self._db.fetchval(
                """
                UPDATE public.settings_documents
                   SET payload = $2::jsonb,
                       revision = revision + 1,
                       updated_at = now(),
                       updated_by = $3
                 WHERE kind = $1
                 RETURNING revision
                """,
                kind,
                _json(documents[kind]),
                actor,
            )
            if revision is None:
                raise ConfigError(f"settings document {kind!r} disappeared during update")
            await self._append_revision(
                kind,
                int(revision),
                documents[kind],
                note,
                actor=actor,
                action="replace",
            )
        return int(revision)

    async def _append_revision(
        self,
        kind: str,
        revision: int,
        payload: Mapping[str, Any],
        note: str | None,
        *,
        actor: str,
        action: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO public.settings_revisions
                (kind, revision, payload, actor, note)
            VALUES ($1, $2, $3::jsonb, $4, $5)
            """,
            kind,
            revision,
            _json(payload),
            actor,
            note,
        )
        await self._db.execute(
            """
            INSERT INTO public.config_audit (kind, revision, action, actor, detail)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            kind,
            revision,
            action,
            actor,
            _json({"note": note} if note else {}),
        )

    def _read_yaml_documents(self) -> dict[str, dict[str, Any]]:
        documents: dict[str, dict[str, Any]] = {}
        for kind in CONFIG_KINDS:
            payload = read_yaml(self._config_dir / f"{kind}.yaml")
            if not isinstance(payload, dict):
                raise ConfigError(f"{self._config_dir / f'{kind}.yaml'} must contain a mapping")
            documents[kind] = payload
        return documents


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise ConfigError(f"stored settings document is not valid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ConfigError("stored settings document is not an object")


def _json(value: Mapping[str, Any]) -> str:
    """Serialize deterministically for the jsonb cast; asyncpg has no codec here."""
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


__all__ = ["CONFIG_KINDS", "SEED_ACTOR", "SettingsStore"]
