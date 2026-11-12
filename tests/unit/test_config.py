"""YAML loading, ${VAR} substitution and tenant resolution."""

import textwrap
from pathlib import Path

import pytest

from switchboard.config import load_settings
from switchboard.core.config import substitute_env, tenant_context
from switchboard.core.errors import ConfigError
from switchboard.core.models import RetrievalSettings

REPO = Path(__file__).resolve().parents[2]

PLATFORM = """
orchestrator: langgraph
tool_sources:
  - name: olist
    adapter: mcp
    url: ${BASE_URL}
    manifest: ${MANIFEST}
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

PRICES = """
verified_on: "2026-11-12"
entries:
  - provider: lmstudio
    model: null
    input_per_million: 0.0
    cached_input_per_million: 0.0
    output_per_million: 0.0
    note: no API cost
"""

MANIFEST = REPO / "mcp_servers" / "olist" / "manifest.yaml"

ENV = {
    "BASE_URL": "http://localhost:1234/v1",
    "MODEL": "qwen3-8b",
    "EMBED_MODEL": "bge-m3",
    "MANIFEST": str(MANIFEST),
}


def write_config(
    directory: Path,
    *,
    platform: str = PLATFORM,
    providers: str = PROVIDERS,
    tenants: str = TENANTS,
    prices: str = PRICES,
) -> Path:
    (directory / "platform.yaml").write_text(textwrap.dedent(platform), encoding="utf-8")
    (directory / "providers.yaml").write_text(textwrap.dedent(providers), encoding="utf-8")
    (directory / "tenants.yaml").write_text(textwrap.dedent(tenants), encoding="utf-8")
    (directory / "prices.yaml").write_text(textwrap.dedent(prices), encoding="utf-8")
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
    assert settings.prices.verified_on.isoformat() == "2026-11-12"
    assert settings.prices.lookup("lmstudio", "qwen3-8b") is not None

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


def test_unknown_agent_name_is_named(tmp_path: Path) -> None:
    tenants = TENANTS.replace('allow: ["direct", "docs"]', 'allow: ["direct", "ghost_agent"]')
    with pytest.raises(ConfigError, match=r"unknown agents.*ghost_agent"):
        load_settings(write_config(tmp_path, tenants=tenants), ENV)


def test_unknown_tool_wire_name_is_named(tmp_path: Path) -> None:
    tenants = TENANTS.replace('allow: ["orders_late@1"]', 'allow: ["ghost_tool@1"]')
    with pytest.raises(ConfigError, match=r"unknown tools.*ghost_tool@1"):
        load_settings(write_config(tmp_path, tenants=tenants), ENV)


def test_tool_without_a_version_is_rejected(tmp_path: Path) -> None:
    tenants = TENANTS.replace('allow: ["orders_late@1"]', 'allow: ["orders_late"]')
    with pytest.raises(ConfigError, match="name@1"):
        load_settings(write_config(tmp_path, tenants=tenants), ENV)


def test_duplicate_tool_and_allow_ask_overlap_are_rejected(tmp_path: Path) -> None:
    duplicate = TENANTS.replace(
        'allow: ["orders_late@1"]', 'allow: ["orders_late@1", "orders_late@1"]'
    )
    with pytest.raises(ConfigError, match="duplicates"):
        load_settings(write_config(tmp_path, tenants=duplicate), ENV)

    overlap = TENANTS.replace(
        'allow: ["orders_late@1"]\n      ask: []',
        'allow: ["orders_late@1"]\n      ask: ["orders_late@1"]',
    )
    with pytest.raises(ConfigError, match="allowed automatically"):
        load_settings(write_config(tmp_path, tenants=overlap), ENV)


def test_seller_ids_must_be_null_or_a_non_empty_unique_list(tmp_path: Path) -> None:
    empty = TENANTS.replace('seller_ids: ["seller-1"]', "seller_ids: []")
    with pytest.raises(ConfigError, match="non-empty list"):
        load_settings(write_config(tmp_path, tenants=empty), ENV)

    duplicate = TENANTS.replace('seller_ids: ["seller-1"]', 'seller_ids: ["seller-1", "seller-1"]')
    with pytest.raises(ConfigError, match="duplicates"):
        load_settings(write_config(tmp_path, tenants=duplicate), ENV)


def test_unknown_tenant_keys_are_rejected(tmp_path: Path) -> None:
    tenants = TENANTS.replace("seller_ids:", "seller_id:")
    with pytest.raises(ConfigError, match="seller_id"):
        load_settings(write_config(tmp_path, tenants=tenants), ENV)


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
    local = settings.prices.lookup("lmstudio", "qwen3-8b")
    assert local is not None and local.is_zero
    deepseek = settings.prices.lookup("deepseek", "deepseek-flash")
    assert deepseek is not None
    assert deepseek.source == "https://api-docs.deepseek.com/quick_start/pricing"
    assert settings.prices.lookup("mock") is None
    assert set(settings.tenants.tenants) == {"default", "seller_a"}
    assert settings.tenants.tenants["seller_a"].seller_ids == ["955fee9216a65b617aa5c0531780ce60"]
    assert settings.tenants.tenants["default"].retrieval.search == "hybrid"
    assert settings.tenants.tenants["default"].retrieval.rewrite is True


def test_a_missing_price_file_is_a_config_error(tmp_path: Path) -> None:
    write_config(tmp_path)
    (tmp_path / "prices.yaml").unlink()
    with pytest.raises(ConfigError, match=r"prices\.yaml"):
        load_settings(tmp_path, ENV)


def test_a_paid_price_row_needs_a_source(tmp_path: Path) -> None:
    prices = """
verified_on: "2026-11-12"
entries:
  - provider: paid
    model: paid-model
    input_per_million: 1.0
    cached_input_per_million: 0.1
    output_per_million: 2.0
"""
    with pytest.raises(ConfigError, match="needs a source"):
        load_settings(write_config(tmp_path, prices=prices), ENV)


def test_duplicate_price_rows_are_rejected(tmp_path: Path) -> None:
    prices = (
        PRICES
        + """
  - provider: lmstudio
    model: null
    input_per_million: 0.0
    cached_input_per_million: 0.0
    output_per_million: 0.0
"""
    )
    with pytest.raises(ConfigError, match="duplicate price entry"):
        load_settings(write_config(tmp_path, prices=prices), ENV)


def test_a_negative_price_is_rejected(tmp_path: Path) -> None:
    prices = PRICES.replace("input_per_million: 0.0", "input_per_million: -1.0")
    with pytest.raises(ConfigError, match="greater than or equal to 0"):
        load_settings(write_config(tmp_path, prices=prices), ENV)


def test_retrieval_settings_default_to_the_phase_one_path() -> None:
    settings = RetrievalSettings()
    assert settings.search == "vector"
    assert settings.rewrite is False
    assert settings.rewrite_mode == "conditional"
    assert settings.min_score == 0.35


def test_unknown_retrieval_search_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="search"):
        RetrievalSettings(search="bm25")  # type: ignore[arg-type]
