"""The normalized control-plane projection (V3.01)."""

from __future__ import annotations

from pathlib import Path

from switchboard.config import load_settings
from switchboard.control_plane import build_control_plane_rows
from switchboard.registry import Registry

REPO = Path(__file__).resolve().parents[2]
ENV = {
    "MCP_OLIST_URL": "http://localhost:8001",
    "MCP_REVIEWS_URL": "http://localhost:8002",
    "LMSTUDIO_BASE_URL": "http://localhost:18080/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
    "SWITCHBOARD_TRACING_EXPORTER": "none",
}


def test_projection_has_providers_models_sources_and_agents() -> None:
    settings = load_settings(REPO / "config", ENV)
    rows = build_control_plane_rows(settings, Registry())
    assert {row[0] for row in rows.providers} >= {"lmstudio", "deepseek"}
    assert any(row[0] == "lmstudio" and row[1] == "qwen3-8b" for row in rows.models)
    assert {row[0] for row in rows.mcp_servers} == {"catalog", "olist", "reviews"}
    assert {row[0] for row in rows.agents} >= {"data", "direct", "docs", "clarify"}
