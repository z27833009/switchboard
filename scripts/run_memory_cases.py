"""Run the V3.16 multi-session memory cases against the local stack."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, cast

PRESET = "direct"
TIMEOUT_S = 180


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.opener.open(request, timeout=TIMEOUT_S) as response:
            return cast(dict[str, Any], json.loads(response.read()))

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(self.base_url + path, method="GET")
        with self.opener.open(request, timeout=TIMEOUT_S) as response:
            return cast(dict[str, Any], json.loads(response.read()))

    def login(self, tenant: str, email: str, password: str) -> None:
        self._post(
            "/auth/login",
            {"tenant_id": tenant, "email": email, "password": password},
        )

    def chat(self, query: str, session_id: str, tenant: str) -> dict[str, Any]:
        return self._post(
            "/chat",
            {
                "query": query,
                "tenant_id": tenant,
                "session_id": session_id,
                "preset": PRESET,
            },
        )

    def settings(self, tenant: str) -> dict[str, Any]:
        return self._get(f"/settings?tenant_id={tenant}")


def accepted_forms(item: str) -> list[str]:
    forms = [item]
    try:
        parsed = date.fromisoformat(item)
    except ValueError:
        return forms
    forms.extend(
        [
            f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}",
            f"{parsed.day} {parsed.strftime('%B')} {parsed.year}",
        ]
    )
    return forms


def passed(answer: str | None, expected: list[str]) -> bool:
    if answer is None:
        return False
    lower = answer.lower()
    return all(any(form.lower() in lower for form in accepted_forms(item)) for item in expected)


def run_case(api: Api, tenant: str, case: dict[str, Any]) -> dict[str, Any]:
    fact_session = str(uuid.uuid4())
    fact = api.chat(
        f"Please remember this fact for later: {case['fact']}",
        fact_session,
        tenant,
    )
    off_session = str(uuid.uuid4())
    off = api.chat(case["question"], off_session, tenant)
    cross = api.chat(case["question"], str(uuid.uuid4()), tenant)
    same = api.chat(case["question"], fact_session, tenant)
    expected = list(case["expected_contains"])
    return {
        "id": case["id"],
        "category": case.get("category"),
        "fact": case["fact"],
        "question": case["question"],
        "expected_contains": expected,
        "fact_session": fact_session,
        "fact_run": fact.get("run_id"),
        "modes": {
            "history_off": {
                "session_id": off_session,
                "run_id": off.get("run_id"),
                "status": off.get("status"),
                "answer": off.get("answer"),
                "passed": passed(off.get("answer"), expected),
            },
            "cross_session": {
                "session_id": None,
                "run_id": cross.get("run_id"),
                "status": cross.get("status"),
                "answer": cross.get("answer"),
                "passed": passed(cross.get("answer"), expected),
            },
            "same_session": {
                "session_id": fact_session,
                "run_id": same.get("run_id"),
                "status": same.get("status"),
                "answer": same.get("answer"),
                "passed": passed(same.get("answer"), expected),
            },
        },
    }


def summary(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    passed_rows = [row for row in rows if row["modes"][mode]["passed"]]
    return {
        "passed": len(passed_rows),
        "total": len(rows),
        "failed_ids": [row["id"] for row in rows if not row["modes"][mode]["passed"]],
    }


def write_report(out: Path, payload: dict[str, Any]) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown = out.with_suffix(".md")
    lines = [
        "# Multi-session memory v1",
        "",
        "Dataset: `evals/datasets/v3/memory-v1.json` (20 author-approved cases).",
        f"Provider: `{payload['provider']}` / `{payload['model']}`. Preset: `{PRESET}`.",
        "",
        "| Mode | Passed | Total |",
        "| --- | ---: | ---: |",
    ]
    for mode, label in (
        ("history_off", "history off, new session"),
        ("cross_session", "layers 1 to 4, cross-session"),
        ("same_session", "layers 1 to 4, same session"),
    ):
        item = payload["summary"][mode]
        lines.append(f"| {label} | {item['passed']} | {item['total']} |")
    lines += [
        "",
        f"T3.11: **{payload['t3_11']}**.",
        "",
        "The cross-session mode asks the question in a session that never saw the fact.",
        "The same-session mode is the within-conversation control: it shows whether the",
        "current layers 1 to 4 preserve the fact when the fact and question share a session.",
        "",
        "## Failures",
        "",
    ]
    for row in payload["cases"]:
        failed = [mode for mode, item in row["modes"].items() if not item["passed"]]
        if failed:
            lines.append(f"### {row['id']}")
            lines.append("")
            lines.append(f"- fact: {row['fact']}")
            lines.append(f"- question: {row['question']}")
            for mode in failed:
                item = row["modes"][mode]
                lines.append(f"- {mode}: {item['status']}, answer: {item['answer']!r}")
            lines.append("")
    markdown.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return markdown


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--email", default="admin@local.invalid")
    parser.add_argument("--password", default=os.environ.get("SWITCHBOARD_ADMIN_PASSWORD", ""))
    args = parser.parse_args()
    if not args.password:
        parser.error("set SWITCHBOARD_ADMIN_PASSWORD or pass --password")

    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    cases = payload["cases"]
    if len(cases) != 20 or any(case.get("review") != "approved" for case in cases):
        parser.error("dataset must contain 20 approved cases")

    api = Api(args.base_url)
    api.login(args.tenant, args.email, args.password)
    settings = api.settings(args.tenant)
    provider = settings["provider"]["name"]
    model = settings["tenant"].get("model") or settings["provider"].get("model")

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case['id']}", flush=True)
        rows.append(run_case(api, args.tenant, case))

    off = summary(rows, "history_off")
    cross = summary(rows, "cross_session")
    same = summary(rows, "same_session")
    # T3.11 is needed when the current layers do not carry a fact across a
    # session boundary. A same-session pass cannot substitute for that.
    t3_11 = "remains deferred" if cross["passed"] < cross["total"] else "not needed by this run"
    report = {
        "version": "memory-v1",
        "dataset": str(args.dataset.as_posix()),
        "tenant": args.tenant,
        "provider": provider,
        "model": model,
        "preset": PRESET,
        "summary": {
            "history_off": off,
            "cross_session": cross,
            "same_session": same,
        },
        "t3_11": t3_11,
        "cases": rows,
    }
    markdown = write_report(args.out, report)
    print(json.dumps(report["summary"], indent=2))
    print(f"T3.11: {t3_11}")
    print(f"wrote {args.out} and {markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
