"""The second tool source: manifest and tenant wiring (T3.08).

The reviews server is TypeScript, but the API reads its manifest exactly like
the Olist one, so the allowlist and `/settings` checks are provider-neutral.
"""

from __future__ import annotations

from pathlib import Path

from switchboard.config import declared_tool_names, load_settings
from switchboard.eval.manifest import load_tool_specs

REPO = Path(__file__).resolve().parents[2]
REVIEWS_MANIFEST = REPO / "mcp_servers" / "reviews" / "manifest.yaml"
ENV = {
    "MCP_OLIST_URL": "http://localhost:8001",
    "MCP_REVIEWS_URL": "http://localhost:8002",
    "LMSTUDIO_BASE_URL": "http://localhost:18080/v1",
    "LMSTUDIO_MODEL": "qwen3-8b",
    "LMSTUDIO_EMBEDDING_MODEL": "bge-m3",
    "DEEPSEEK_API_KEY": "",
}


def test_the_reviews_manifest_loads_two_read_only_tenant_scoped_tools() -> None:
    specs = load_tool_specs(REVIEWS_MANIFEST)
    assert [spec.wire_name for spec in specs] == ["order_reviews@1", "review_summary@1"]
    for spec in specs:
        assert spec.risk == "read"
        assert spec.tenant_scoped is True
        assert spec.input_schema["additionalProperties"] is False
    summary = next(spec for spec in specs if spec.name == "review_summary")
    assert summary.input_schema["required"] == ["date_from", "date_to"]
    assert summary.input_schema["properties"]["min_score"]["maximum"] == 5
    reviews = next(spec for spec in specs if spec.name == "order_reviews")
    assert reviews.input_schema["required"] == ["order_id"]


def test_the_committed_config_declares_both_sources() -> None:
    settings = load_settings(REPO / "config", ENV)
    names = {source.name for source in settings.platform.tool_sources}
    assert names == {"olist", "reviews"}
    reviews = next(s for s in settings.platform.tool_sources if s.name == "reviews")
    assert reviews.adapter == "mcp"
    assert reviews.url == "http://localhost:8002/mcp"
    assert reviews.manifest == "mcp_servers/reviews/manifest.yaml"
    declared = declared_tool_names(settings)
    assert {"review_summary@1", "order_reviews@1"} <= declared


def test_only_seller_a_allows_the_review_tools() -> None:
    settings = load_settings(REPO / "config", ENV)
    default = settings.tenants.tenants["default"]
    seller = settings.tenants.tenants["seller_a"]
    assert "review_summary@1" not in default.tools.allow
    assert "order_reviews@1" not in default.tools.allow
    assert {
        "review_summary@1",
        "order_reviews@1",
    } <= set(seller.tools.allow)
    # Exactly the three Olist tools plus the two reviews, nothing else.
    assert set(seller.tools.allow) == {
        "orders_summary@1",
        "order_lookup@1",
        "top_products@1",
        "review_summary@1",
        "order_reviews@1",
    }
    assert seller.seller_ids == ["955fee9216a65b617aa5c0531780ce60"]
