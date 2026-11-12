"""CLI smoke test: mock replay, one case, no database and no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner, Result

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
            },
            "deepseek": {
                "adapter": "openai_compatible",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "",
                "model": "deepseek-flash",
                "capabilities": {
                    "tools": True,
                    "json_schema": False,
                    "streaming": True,
                    "reasoning": True,
                    "context_window": 1000000,
                },
            },
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
        "cost_cap_usd": 5.0,
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
            },
            {
                "provider": "deepseek",
                "model": "deepseek-flash",
                "input_per_million": 0.30,
                "cached_input_per_million": 0.006,
                "output_per_million": 1.20,
                "source": "https://api-docs.deepseek.com/quick_start/pricing",
            },
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


def invoke_deepseek(
    tmp_path: Path,
    *extra: str,
) -> Result:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    write_config(config_dir)
    return CliRunner().invoke(
        app,
        [
            "eval",
            "run",
            "--suite",
            "routing",
            "--provider",
            "deepseek",
            "--config-dir",
            str(config_dir),
            "--dataset-dir",
            str(REPO_ROOT / "evals" / "datasets"),
            "--manifest",
            str(REPO_ROOT / "mcp_servers" / "olist" / "manifest.yaml"),
            "--output-dir",
            str(tmp_path / "reports"),
            "--limit",
            "1",
            *extra,
        ],
    )


def test_a_paid_run_prints_the_estimate_and_refuses_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = invoke_deepseek(tmp_path)

    assert result.exit_code == 1
    assert "cost estimate: provider deepseek" in result.output
    assert "1 case," in result.output
    assert "model deepseek-flash" in result.output
    assert "estimated 0.002400 USD" in result.output
    assert "--confirm-cost" in result.output
    assert "needs an api_key" not in result.output


def test_an_estimate_above_the_cap_is_refused_even_with_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    result = invoke_deepseek(tmp_path, "--confirm-cost", "--cost-cap-usd", "0.000001")

    assert result.exit_code == 1
    assert "exceeds cost_cap_usd" in result.output
    assert "needs an api_key" not in result.output


def test_confirmation_proceeds_to_provider_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = invoke_deepseek(tmp_path, "--confirm-cost")

    assert result.exit_code == 1
    assert "needs an api_key" in result.output
    assert "cost estimate" in result.output
