"""Judge agreement against the author's human labels (T3.04, design 6.8).

The sample file is read, never rewritten. These tests pin the agreement math,
the refusal when a label is missing, and the report the command writes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from switchboard.cli import app
from switchboard.core.config import PriceEntry
from switchboard.core.errors import ConfigError
from switchboard.core.models import JudgeRequest, JudgeVerdict, Usage
from switchboard.eval.judge_agreement import (
    AgreementCase,
    AgreementRun,
    build_agreement_report,
    load_agreement,
    render_agreement_markdown,
    run_agreement,
    write_agreement_report,
)
from switchboard.eval.scorers.judge import (
    agreement_summary,
    cohen_kappa,
    confusion_matrix,
    judge_metric,
    raw_agreement,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE = REPO_ROOT / "evals" / "judge" / "agreement.jsonl"
ZERO_PRICE = PriceEntry(
    provider="lmstudio",
    model=None,
    input_per_million=0.0,
    cached_input_per_million=0.0,
    output_per_million=0.0,
)


class FakeJudge:
    name = "fake-judge"

    def __init__(self, verdicts: dict[str, str], *, error: Exception | None = None) -> None:
        self.verdicts = verdicts
        self.error = error
        self.requests: list[JudgeRequest] = []
        # The case id is carried in the query by the test helper below.
        self.seen: list[str] = []

    async def judge(self, request: JudgeRequest) -> JudgeVerdict:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        case_id = request.query.split()[0]
        self.seen.append(case_id)
        label = self.verdicts.get(case_id, "acceptable")
        return JudgeVerdict(
            verdict=label,  # type: ignore[arg-type]
            score=0.9 if label == "acceptable" else 0.1,
            reason=f"scripted {label}",
            usage=Usage(input_tokens=100, output_tokens=10),
            latency_ms=7,
            cost_usd=0.0,
        )


def case(case_id: str, label: str = "acceptable") -> AgreementCase:
    return AgreementCase(
        case_id=case_id,
        query=f"{case_id} what is the return window?",
        answer="Returns take 15 days [d1].",
        evidence=[],
        citations=[],
        human_label=label,  # type: ignore[arg-type]
    )


def test_the_sample_has_the_twelve_author_labels() -> None:
    cases = load_agreement(SAMPLE)

    assert len(cases) == 12
    acceptable = [item.case_id for item in cases if item.human_label == "acceptable"]
    weak = [item.case_id for item in cases if item.human_label == "weak"]
    assert acceptable == ["g025", "g023", "g014", "g019", "g022", "g030", "g011", "g008", "g020"]
    assert weak == ["g007", "g026", "g024"]
    # The honest partials are acceptable in this rubric, unlike in the gate set.
    by_id = {item.case_id: item for item in cases}
    assert by_id["g019"].human_label == "acceptable"
    assert by_id["g022"].human_label == "acceptable"


def test_a_missing_human_label_refuses_the_file(tmp_path: Path) -> None:
    path = tmp_path / "agreement.jsonl"
    path.write_text(
        json.dumps({"id": "x1", "query": "q", "answer": "a", "evidence": []}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="human_label"):
        load_agreement(path)


def test_an_unknown_human_label_refuses_the_file(tmp_path: Path) -> None:
    path = tmp_path / "agreement.jsonl"
    path.write_text(
        json.dumps({"id": "x1", "query": "q", "answer": "a", "human_label": "maybe"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="human_label"):
        load_agreement(path)


def test_a_duplicate_case_id_is_refused(tmp_path: Path) -> None:
    row = {"id": "x1", "query": "q", "answer": "a", "human_label": "weak"}
    path = tmp_path / "agreement.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="duplicate"):
        load_agreement(path)


def test_agreement_math_on_a_known_sample() -> None:
    pairs = [("acceptable", "acceptable"), ("acceptable", "weak"), ("weak", "weak")]

    assert raw_agreement(pairs) == pytest.approx(0.6667)
    # po = 2/3, pe = (2/3)(1/3) + (1/3)(2/3) = 4/9, so kappa = 2/5.
    assert cohen_kappa(pairs) == pytest.approx(0.4)
    matrix = confusion_matrix(pairs)
    assert matrix == {
        "acceptable": {"acceptable": 1, "weak": 1},
        "weak": {"acceptable": 0, "weak": 1},
    }
    summary = agreement_summary(pairs)
    assert summary["cases"] == 3
    assert summary["agreements"] == 2
    assert summary["false_rejections"] == 1
    assert summary["missed_weak"] == 0
    assert summary["judge_accept_rate"] == pytest.approx(0.3333)


def test_an_unknown_label_in_the_math_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="human label"):
        confusion_matrix([("great", "weak")])


def test_judge_metric_maps_labels_and_errors() -> None:
    assert judge_metric("acceptable") == 1.0
    assert judge_metric("weak") == 0.0
    assert judge_metric(None) is None


async def test_run_agreement_asks_every_case_once() -> None:
    cases = [case("c1"), case("c2", "weak")]
    judge = FakeJudge({"c1": "acceptable", "c2": "weak"})

    runs = await run_agreement(judge, cases)

    assert [run.verdict.verdict for run in runs if run.verdict] == ["acceptable", "weak"]
    assert len(judge.requests) == 2
    assert judge.requests[0].answer == "Returns take 15 days [d1]."


def report_for(runs: list[AgreementRun], *, enabled: bool = False) -> dict[str, Any]:
    cases = [run.case for run in runs]
    return build_agreement_report(
        cases=cases,
        runs=runs,
        dataset_path="evals/judge/agreement.jsonl",
        provider="lmstudio",
        model="qwen3-8b",
        judge_name="fake-judge",
        threshold=0.5,
        price=ZERO_PRICE,
        judge_enabled=enabled,
        generated_at=datetime(2026, 11, 13, tzinfo=UTC),
        wall_time_s=1.5,
    )


def verdict_for(case_id: str, label: str, human: str) -> AgreementRun:
    return AgreementRun(
        case=case(case_id, human),
        verdict=JudgeVerdict(
            verdict=label,  # type: ignore[arg-type]
            score=0.9 if label == "acceptable" else 0.2,
            reason=f"scripted {label}",
            usage=Usage(input_tokens=50, output_tokens=5),
            latency_ms=10,
            cost_usd=0.0,
        ),
    )


def test_the_report_carries_exact_agreement_and_labels() -> None:
    runs = [
        verdict_for("g025", "acceptable", "acceptable"),
        verdict_for("g019", "weak", "acceptable"),
        verdict_for("g007", "weak", "weak"),
    ]

    report = report_for(runs)
    written = json.loads(json.dumps(report))

    assert written["metrics"]["raw_agreement"] == pytest.approx(0.6667)
    assert written["confusion_matrix"]["acceptable"]["weak"] == 1
    assert written["false_rejection_cases"] == ["g019"]
    assert written["missed_weak_cases"] == []
    assert written["labels"] == {"acceptable": ["g025", "g019"], "weak": ["g007"]}
    assert written["decision"]["met"] is False
    assert written["decision"]["judge_enabled_in_config"] is False
    assert written["cost_usd"] == 0.0
    assert written["no_api_cost"] is True
    assert written["usage"] == {
        "input_tokens": 150,
        "cached_input_tokens": 0,
        "output_tokens": 15,
        "total_tokens": 165,
    }
    markdown = render_agreement_markdown(report)
    assert "| Human \\ Judge | acceptable | weak |" in markdown
    assert "g019" in markdown
    assert "does not meet the bar" in markdown
    assert "no API cost" in markdown


def test_a_full_agreement_run_meets_the_bar() -> None:
    runs = [verdict_for("g025", "acceptable", "acceptable"), verdict_for("g007", "weak", "weak")]

    report = report_for(runs, enabled=True)

    assert report["metrics"]["raw_agreement"] == 1.0
    assert report["metrics"]["cohen_kappa"] == 1.0
    assert report["decision"]["met"] is True
    assert report["decision"]["judge_enabled_in_config"] is True


def test_a_judge_error_is_reported_and_never_scored() -> None:
    runs = [
        verdict_for("g025", "acceptable", "acceptable"),
        AgreementRun(case=case("g007", "weak"), error="JudgeError: bad output"),
    ]

    report = report_for(runs)

    assert report["metrics"]["cases"] == 1
    assert report["metrics"]["errors"] == 1
    assert report["error_case_ids"] == ["g007"]
    assert report["decision"]["met"] is False
    assert report["cases"][1]["judge_label"] is None
    assert "JudgeError" in report["cases"][1]["error"]


def test_the_report_files_are_written(tmp_path: Path) -> None:
    runs = [verdict_for("g025", "acceptable", "acceptable")]

    markdown, payload = write_agreement_report(report_for(runs), tmp_path)

    assert markdown.name == "judge-agreement.md"
    assert payload.name == "judge-agreement.json"
    assert "Judge agreement" in markdown.read_text(encoding="utf-8")
    assert json.loads(payload.read_text(encoding="utf-8"))["suite"] == "judge-agreement"


def test_the_command_refuses_a_sample_without_labels(tmp_path: Path) -> None:
    path = tmp_path / "agreement.jsonl"
    path.write_text(json.dumps({"id": "x1", "query": "q", "answer": "a"}) + "\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, ["eval", "judge-agreement", "--dataset", str(path)])

    assert result.exit_code == 1
    assert "human_label" in result.stderr


def test_the_command_refuses_a_paid_judge_provider(tmp_path: Path) -> None:
    """No paid API call: a nonzero price row stops the run before any request."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "platform.yaml").write_text(
        "orchestrator: langgraph\nretriever: none\nembedder: mock\njudge:\n  enabled: false\n"
        "  provider: paid\n",
        encoding="utf-8",
    )
    (config_dir / "providers.yaml").write_text(
        "providers:\n"
        "  paid:\n"
        "    adapter: openai_compatible\n"
        "    base_url: https://example.invalid/v1\n"
        "    api_key: x\n"
        "    model: paid-model\n"
        "    capabilities: {tools: true, json_schema: false, streaming: false, "
        "reasoning: false, context_window: 8192}\n"
        "embedders:\n"
        "  mock: {adapter: mock, dimension: 1024}\n",
        encoding="utf-8",
    )
    (config_dir / "tenants.yaml").write_text(
        "tenants:\n  default:\n    provider: paid\n    agents: {allow: [direct]}\n",
        encoding="utf-8",
    )
    (config_dir / "prices.yaml").write_text(
        "verified_on: 2026-11-12\n"
        "entries:\n"
        "  - provider: paid\n"
        "    input_per_million: 1.0\n"
        "    cached_input_per_million: 0.0\n"
        "    output_per_million: 2.0\n"
        "    source: https://example.invalid/prices\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "eval",
            "judge-agreement",
            "--dataset",
            str(SAMPLE),
            "--config-dir",
            str(config_dir),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )

    assert result.exit_code == 1
    assert "must run on a provider with no API cost" in result.stderr
    assert not (tmp_path / "reports").exists()
