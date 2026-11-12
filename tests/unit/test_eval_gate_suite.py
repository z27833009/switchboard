"""Gate suite: 30 constructed cases, per-check metrics and both report files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from switchboard.core.models import (
    AgentSpec,
    CompletionRequest,
    CompletionResponse,
    ProviderCapabilities,
)
from switchboard.core.ports import EvalDeps
from switchboard.eval.report import ReportMeta, build_report, render_markdown, write_report
from switchboard.eval.runner import run_cases
from switchboard.eval.suites.gate import GateSuite

REPO_ROOT = Path(__file__).resolve().parents[2]


class NoModel:
    name = "none"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            tools=False,
            json_schema=False,
            streaming=False,
            reasoning=False,
            context_window=8192,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise AssertionError("the gate suite must not call a model")


def specs() -> list[AgentSpec]:
    return [
        AgentSpec(name="data", description="orders", cacheable=False, needs=["tools"]),
        AgentSpec(name="direct", description="small talk", cacheable=True, exclusive=True),
        AgentSpec(name="docs", description="policies", cacheable=True, needs=["retriever"]),
    ]


async def report_for_gate() -> dict[str, Any]:
    suite = GateSuite(agent_specs=specs(), dataset_root=REPO_ROOT / "evals" / "datasets")
    cases = suite.load("v2")
    results = await run_cases(suite, cases, EvalDeps(provider=NoModel()), concurrency=4)
    return build_report(
        ReportMeta(
            suite="gate",
            dataset_version="v2",
            dataset_path="evals/datasets/v2/gate.jsonl",
            split="test",
            orchestrator="langgraph",
            provider="mock",
            model="deterministic",
            generated_at=datetime(2026, 11, 12, 12, 0, tzinfo=UTC),
            wall_time_s=0.02,
            dataset_case_count=30,
            split_case_count=30,
            smoke=False,
        ),
        results,
    )


async def test_gate_suite_loads_30_test_cases() -> None:
    suite = GateSuite(agent_specs=specs(), dataset_root=REPO_ROOT / "evals" / "datasets")
    cases = suite.load("v2")
    assert len(cases) == 30
    assert all(case.split == "test" for case in cases)
    assert {case.id for case in cases} == {f"g{index:03d}" for index in range(1, 31)}


async def test_gate_report_has_per_check_precision_and_recall() -> None:
    report = await report_for_gate()
    metrics = report["metrics"]
    assert report["dataset_version"] == "v2"
    assert report["case_count"] == 30
    assert metrics["numbers_grounded_precision"] == 0.9
    assert metrics["numbers_grounded_recall"] == 1.0
    assert metrics["citations_valid_precision"] == 1.0
    assert metrics["citations_valid_recall"] == 1.0
    assert metrics["evidence_present_precision"] == 1.0
    assert metrics["evidence_present_recall"] == 1.0
    assert metrics["merge_complete_precision"] == 1.0
    assert metrics["merge_complete_recall"] == 1.0
    assert metrics["case_accuracy"] == 0.9667

    counts = report["gate_counts"]
    assert counts["checks"]["numbers_grounded"] == {"tp": 9, "fp": 1, "fn": 0, "tn": 20}
    assert counts["checks"]["citations_valid"] == {"tp": 3, "fp": 0, "fn": 0, "tn": 27}
    assert counts["checks"]["evidence_present"] == {"tp": 4, "fp": 0, "fn": 0, "tn": 26}
    assert counts["checks"]["merge_complete"] == {"tp": 2, "fp": 0, "fn": 0, "tn": 28}
    assert counts["false_positive_cases"] == ["g013"]
    assert counts["false_negative_cases"] == []


async def test_gate_markdown_states_the_dataset_and_label_source() -> None:
    report = await report_for_gate()
    markdown = render_markdown(report)
    assert "# Gate eval: mock" in markdown
    assert "`v2`" in markdown
    assert "Deterministic suite" in markdown
    assert "| numbers_grounded | 9 | 1 | 0 | 20 | 0.9000 | 1.0000 |" in markdown
    assert "not by an independent human annotator" in markdown
    assert "false positive ids: g013" in markdown
    assert "| g013 |" in markdown


async def test_gate_report_writes_both_files(tmp_path: Path) -> None:
    report = await report_for_gate()
    markdown_path, json_path = write_report(report, tmp_path)
    assert markdown_path.name == "gate-mock.md"
    assert json_path.name == "gate-mock.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["dataset_version"] == "v2"
    assert payload["deterministic"] is True
    assert len(payload["cases"]) == 30
