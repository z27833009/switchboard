"""YAML loading, ${VAR} substitution and tenant resolution."""

import textwrap
from pathlib import Path

import pytest

from switchboard.config import load_settings
from switchboard.core.config import substitute_env, tenant_context
from switchboard.core.errors import ConfigError

REPO = Path(__file__).resolve().parents[2]

PLATFORM = """
orchestrator: langgraph
tool_sources:
  - name: olist
    adapter: mcp
    url: ${BASE_URL}
retriever: pgvector
cache: none
embedder: lmstudio
limits:
  max_plan_size: 2
  fallback_plan: ["docs", "data"]
"""

PROVIDERS = """
providers:
  lmstudio:
    adapter: openai_compatible
    base_url: ${BASE_URL}
    api_key: lm-studio
    model: ${MODEL}
embedders:
  lmstudio:
    adapter: openai_compatible
    base_url: ${BASE_URL}
    model: ${EMBED_MODEL}
    dimension: 8
"""

TENANTS = """
tenants:
  default:
    provider: lmstudio
    agents:
      allow: ["direct", "docs"]
    tools:
      allow: ["orders_late@1"]
      ask: []
    seller_ids: ["seller-1"]
"""

ENV = {
    "BASE_URL": "http://localhost:1234/v1",
    "MODEL": "qwen3-8b",
    "EMBED_MODEL": "bge-m3",
}


def write_config(
    directory: Path, *, platform: str = PLATFORM, providers: str = PROVIDERS, tenants: str = TENANTS
) -> Path:
    (directory / "platform.yaml").write_text(textwrap.dedent(platform), encoding="utf-8")
    (directory / "providers.yaml").write_text(textwrap.dedent(providers), encoding="utf-8")
    (directory / "tenants.yaml").write_text(textwrap.dedent(tenants), encoding="utf-8")
    return directory


def test_substitute_env_walks_nested_values() -> None:
    value = {"url": "${BASE_URL}", "items": ["${MODEL}", 3], "flag": True}
    assert substitute_env(value, ENV) == {
        "url": "http://localhost:1234/v1",
        "items": ["qwen3-8b", 3],
        "flag": True,
    }


def test_missing_environment_variable_is_named() -> None:
    with pytest.raises(ConfigError, match="MISSING_VAR"):
        substitute_env("${MISSING_VAR}", ENV)


def test_load_settings_substitutes_and_validates(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path), ENV)
    assert settings.platform.orchestrator == "langgraph"
    assert settings.platform.tool_sources[0].url == "http://localhost:1234/v1"
    assert settings.platform.limits.max_plan_size == 2
    assert settings.providers.providers["lmstudio"].model == "qwen3-8b"
    assert settings.providers.embedders["lmstudio"].dimension == 8

    context = tenant_context("default", settings.tenants)
    assert context.allowed_agents == {"direct", "docs"}
    assert context.allowed_tools == {"orders_late@1"}
    assert context.seller_ids == ["seller-1"]


def test_missing_variable_in_a_file_is_a_config_error(tmp_path: Path) -> None:
    write_config(tmp_path)
    env = dict(ENV)
    del env["MODEL"]
    with pytest.raises(ConfigError, match="MODEL"):
        load_settings(tmp_path, env)


def test_unknown_tenant_is_a_config_error(tmp_path: Path) -> None:
    settings = load_settings(write_config(tmp_path), ENV)
    with pytest.raises(ConfigError, match="unknown tenant"):
        tenant_context("ghost", settings.tenants)


def test_tenant_provider_must_exist(tmp_path: Path) -> None:
    tenants = TENANTS.replace("provider: lmstudio", "provider: ghost")
    with pytest.raises(ConfigError, match="unknown provider"):
        load_settings(write_config(tmp_path, tenants=tenants), ENV)


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    platform = PLATFORM + "  typo_field: 1\n"
    with pytest.raises(ConfigError, match="typo_field"):
        load_settings(write_config(tmp_path, platform=platform), ENV)


def test_committed_config_files_load() -> None:
    settings = load_settings(
        REPO / "config",
        {
            "LMSTUDIO_BASE_URL": "http://host.docker.internal:18080/v1",
            "LMSTUDIO_MODEL": "qwen3-8b",
            "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
            "MCP_OLIST_URL": "http://localhost:8001",
        },
    )
    assert settings.platform.limits.fallback_plan == ["docs", "data"]
    assert settings.platform.embedder == "lmstudio"
    assert set(settings.tenants.tenants) == {"default", "seller_a"}
    assert settings.tenants.tenants["seller_a"].seller_ids == ["3442f8959a84dea7ee197c632cb2df15"]
