"""Measure the core token estimate against LM Studio's prompt token usage.

The ten prompts are Chinese dev cases from the V3.12 calibration set. The script
sends only the user message, so the reported prompt tokens contain the chat
template plus the prompt, not the system prompt or tool schemas.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from switchboard.core.context import estimate_tokens


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def fit(points: list[tuple[int, int]]) -> tuple[float, float]:
    n = len(points)
    sum_x = sum(x for x, _ in points)
    sum_y = sum(y for _, y in points)
    sum_xx = sum(x * x for x, _ in points)
    sum_xy = sum(x * y for x, y in points)
    denominator = n * sum_xx - sum_x * sum_x
    if denominator == 0:
        return 1.0, 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denominator
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evals/datasets/v3/token-calibration.json"),
    )
    parser.add_argument("--out", type=Path, default=Path("evals/reports/token-estimate-v1.json"))
    parser.add_argument("--base-url", default=os.environ.get("LMSTUDIO_BASE_URL", ""))
    parser.add_argument("--model", default=os.environ.get("LMSTUDIO_MODEL", ""))
    parser.add_argument("--api-key", default=os.environ.get("LMSTUDIO_API_KEY", "lm-studio"))
    args = parser.parse_args()
    if not args.base_url or not args.model:
        parser.error("set LMSTUDIO_BASE_URL and LMSTUDIO_MODEL")
    base = args.base_url.rstrip("/")
    endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = payload["cases"]

    rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=120.0) as client:
        for case in cases:
            query = case["query"]
            estimated = estimate_tokens(query)
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {args.api_key}"},
                json={
                    "model": args.model,
                    "messages": [{"role": "user", "content": query}],
                    "max_tokens": 1,
                    "temperature": 0,
                    "stream": False,
                },
            )
            response.raise_for_status()
            body = response.json()
            actual = int(body["usage"]["prompt_tokens"])
            rows.append(
                {
                    "id": case["id"],
                    "query": query,
                    "characters": len(query),
                    "estimated": estimated,
                    "actual": actual,
                    "raw_relative_error": abs(actual - estimated) / actual,
                }
            )
            print(f"{case['id']}: estimated={estimated} actual={actual}", flush=True)

    slope, intercept = fit([(row["estimated"], row["actual"]) for row in rows])
    for row in rows:
        predicted = slope * row["estimated"] + intercept
        row["calibrated"] = round(predicted, 2)
        row["calibrated_relative_error"] = abs(row["actual"] - predicted) / row["actual"]
    raw_errors = [row["raw_relative_error"] for row in rows]
    calibrated_errors = [row["calibrated_relative_error"] for row in rows]
    summary = {
        "cases": len(rows),
        "fit_slope": round(slope, 4),
        "fit_intercept_tokens": round(intercept, 4),
        "raw_p50_relative_error": round(percentile(raw_errors, 0.50), 4),
        "raw_p95_relative_error": round(percentile(raw_errors, 0.95), 4),
        "raw_max_relative_error": round(max(raw_errors), 4),
        "calibrated_p50_relative_error": round(percentile(calibrated_errors, 0.50), 4),
        "calibrated_p95_relative_error": round(percentile(calibrated_errors, 0.95), 4),
        "calibrated_max_relative_error": round(max(calibrated_errors), 4),
    }
    report = {
        "version": payload["version"],
        "provider": "lmstudio",
        "model": args.model,
        "summary": summary,
        "cases": rows,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown = args.out.with_suffix(".md")
    lines = [
        "# Token estimate calibration v1",
        "",
        f"Provider: `lmstudio` / `{args.model}`. Dataset: `{args.dataset.as_posix()}` "
        "(10 dev cases).",
        "Each request sends only the Chinese user message, so the reported prompt tokens are the",
        "chat template plus that message; the system prompt and tool schemas are not included.",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Raw p50 relative error | {summary['raw_p50_relative_error']:.2%} |",
        f"| Raw p95 relative error | {summary['raw_p95_relative_error']:.2%} |",
        f"| Raw maximum relative error | {summary['raw_max_relative_error']:.2%} |",
        f"| Fit slope | {summary['fit_slope']} |",
        f"| Fit intercept | {summary['fit_intercept_tokens']} tokens |",
        f"| Calibrated p50 relative error | {summary['calibrated_p50_relative_error']:.2%} |",
        f"| Calibrated p95 relative error | {summary['calibrated_p95_relative_error']:.2%} |",
        f"| Calibrated maximum relative error | {summary['calibrated_max_relative_error']:.2%} |",
        "",
        "| Case | Characters | Estimated | Actual | Raw error | Calibrated | Calibrated error |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            (
                "| {id} | {characters} | {estimated} | {actual} | {raw:.2%} | "
                "{calibrated} | {cal:.2%} |"
            ).format(
                id=row["id"],
                characters=row["characters"],
                estimated=row["estimated"],
                actual=row["actual"],
                raw=row["raw_relative_error"],
                calibrated=row["calibrated"],
                cal=row["calibrated_relative_error"],
            )
        )
    lines += [
        "",
        "The estimate counts CJK characters as one token each and other characters at three",
        "characters per token, plus four tokens per message. The fit is a linear correction",
        "on this dev set; the report keeps both the raw and calibrated errors so the gap is",
        "visible instead of hiding the template overhead.",
        "",
    ]
    markdown.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"wrote {args.out} and {markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
