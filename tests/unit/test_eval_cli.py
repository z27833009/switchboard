"""CLI smoke test: mock replay, one case, no database and no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from switchboard.cli import app

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "evals" / "fixtures" / "mock_provider"


def write_config(directory: Path) -> None:
    providers = {
        "providers": {
            "mock": {
                "adapter": "mock",
                "model": "qwen3-8b",
                "fixtures": FIXTURES.as_posix(),
            }
        },
        "embedders": {"mock": {"adapter": "mock", "dimension": 1024}},
    }
    tenants = {
        "tenants": {
            "default": {
                "provider": "mock",
                "agents": {"allow": ["direct", "docs", "data"]},
                "tools": {
                    "allow": [
                        "orders_summary@1",
                        "orders_late@1",
                        "order_lookup@1",
                        "top_products@1",
                        "describe_schema@1",
                    ]
                },
            }
        }
    }
    platform = {
        "orchestrator": "langgraph",
        "retriever": "pgvector",
        "cache": "none",
        "embedder": "mock",
        "tracing": {"exporter": "none"},
        "limits": {"max_plan_size": 3},
    }
    prices = {
        "verified_on": "2026-11-12",
        "entries": [
            {
                "provider": "lmstudio",
                "model": None,
                "input_per_million": 0.0,
                "cached_input_per_million": 0.0,
                "output_per_million": 0.0,
                "note": "no API cost",
            }
        ],
    }
    for name, payload in (
        ("providers.yaml", providers),
        ("tenants.yaml", tenants),
        ("platform.yaml", platform),
        ("prices.yaml", prices),
    ):
        (directory / name).write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


@pytest.mark.parametrize("suite", ["routing", "toolcall"])
def test_cli_mock_smoke_writes_reports(
    suite: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)
    output_dir = tmp_path / "reports"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "--suite",
            suite,
            "--provider",
            "mock",
            "--config-dir",
            str(config_dir),
            "--dataset-dir",
            str(REPO_ROOT / "evals" / "datasets"),
            "--manifest",
            str(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml"),
            "--output-dir",
            str(output_dir),
            "--limit",
            "1",
            "--timeout",
            "10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "SMOKE RUN" in result.output
    markdown_path = output_dir / f"{suite}-mock.md"
    json_path = output_dir / f"{suite}-mock.json"
    assert markdown_path.is_file()
    assert json_path.is_file()

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["suite"] == suite
    assert report["dataset_version"] == "v1"
    assert report["provider"] == "mock"
    assert report["model"] == "qwen3-8b"
    assert report["case_count"] == 1
    assert report["smoke"] is True
    assert report["recording"] is False
    assert report["cost_usd"] is None
    assert report["error_count"] == 0
    metric = "plan_accuracy" if suite == "routing" else "tool_name_accuracy"
    assert metric in report["metrics"]
    assert "Smoke run" in markdown_path.read_text(encoding="utf-8")


def test_cli_gate_suite_writes_a_deterministic_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    write_config(config_dir)
    output_dir = tmp_path / "reports"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "--suite",
            "gate",
            "--dataset-version",
            "v2",
            "--provider",
            "mock",
            "--config-dir",
            str(config_dir),
            "--dataset-dir",
            str(REPO_ROOT / "evals" / "datasets"),
            "--manifest",
            str(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml"),
            "--output-dir",
            str(output_dir),
            "--limit",
            "1",
            "--timeout",
            "10",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads((output_dir / "gate-mock.json").read_text(encoding="utf-8"))
    assert report["suite"] == "gate"
    assert report["dataset_version"] == "v2"
    assert report["model"] == "deterministic"
    assert report["deterministic"] is True
    assert report["case_count"] == 1
    assert "numbers_grounded_precision" in report["metrics"]
