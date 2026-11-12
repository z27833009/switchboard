"""Validate dataset files. Standard library only.

    python evals/scripts/validate.py questions   # routing.questions.jsonl + routing.generator_labels.jsonl
    python evals/scripts/validate.py routing     # merged routing.jsonl
    python evals/scripts/validate.py toolcall    # toolcall.jsonl

Errors make the exit code non-zero. Warnings report distribution targets that are missed.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date

from common import (
    AGENTS,
    DATASET_DIR,
    DATE_MAX,
    DATE_MIN,
    EXCLUSIVE_AGENTS,
    METRICS,
    STATUSES,
    TOOLS,
    plan_label,
    read_jsonl,
)

ROUTING_TARGETS = {"direct": 10, "docs": 16, "data": 18, "data+docs": 16}
ROUTING_TOLERANCE = 3
QUESTION_TAGS = {"zh", "trap", "typo"}

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


def check_ids(rows: list[dict], prefix: str, name: str) -> None:
    ids = [r.get("id") for r in rows]
    for i in ids:
        if not isinstance(i, str) or not i.startswith(prefix):
            err(f"{name}: bad id {i!r}, expected prefix {prefix!r}")
    for i, n in Counter(ids).items():
        if n > 1:
            err(f"{name}: duplicate id {i}")


def check_agents(agents: object, where: str) -> list[str] | None:
    if not isinstance(agents, list) or not agents:
        err(f"{where}: agents must be a non-empty list")
        return None
    bad = [a for a in agents if a not in AGENTS]
    if bad:
        err(f"{where}: unknown agents {bad}")
    if len(set(agents)) != len(agents):
        err(f"{where}: duplicate agents")
    if set(agents) & EXCLUSIVE_AGENTS and len(agents) > 1:
        err(f"{where}: {sorted(set(agents) & EXCLUSIVE_AGENTS)} must be alone")
    return agents


def check_distribution(labels: list[list[str]], name: str) -> None:
    counts = Counter(plan_label(a) for a in labels)
    print(f"{name}: plan distribution {dict(sorted(counts.items()))}")
    for plan, target in ROUTING_TARGETS.items():
        if abs(counts.get(plan, 0) - target) > ROUTING_TOLERANCE:
            warn(
                f"{name}: {plan} has {counts.get(plan, 0)}, target {target} +/- {ROUTING_TOLERANCE}"
            )


def check_questions() -> None:
    qpath = DATASET_DIR / "routing.questions.jsonl"
    lpath = DATASET_DIR / "routing.generator_labels.jsonl"
    questions = read_jsonl(qpath)
    labels = read_jsonl(lpath)
    check_ids(questions, "r", qpath.name)
    check_ids(labels, "r", lpath.name)
    if len(questions) != 60:
        err(f"{qpath.name}: {len(questions)} questions, expected 60")
    texts = Counter(q.get("query", "").strip().lower() for q in questions)
    for t, n in texts.items():
        if n > 1:
            err(f"{qpath.name}: duplicate query {t!r}")
    tags = Counter()
    for q in questions:
        if set(q) - {"id", "query", "tags"}:
            err(
                f"{qpath.name} {q.get('id')}: unexpected fields {sorted(set(q) - {'id', 'query', 'tags'})} (labels go in the other file)"
            )
        if not isinstance(q.get("query"), str) or not q["query"].strip():
            err(f"{qpath.name} {q.get('id')}: empty query")
        for t in q.get("tags", []):
            if t not in QUESTION_TAGS:
                err(f"{qpath.name} {q.get('id')}: unknown tag {t!r}")
            tags[t] += 1
    print(f"{qpath.name}: tags {dict(tags)}")
    if tags["zh"] < 10:
        warn(f"only {tags['zh']} questions tagged zh, target 12")
    if tags["trap"] < 10:
        warn(f"only {tags['trap']} questions tagged trap, target at least 10")
    qids = {q.get("id") for q in questions}
    lids = {label.get("id") for label in labels}
    if qids != lids:
        err(
            f"ids differ between files: only in questions {sorted(qids - lids)}, only in labels {sorted(lids - qids)}"
        )
    agents = [
        a
        for label in labels
        if (a := check_agents(label.get("agents"), f"{lpath.name} {label.get('id')}"))
    ]
    check_distribution(agents, lpath.name)


def check_routing() -> None:
    path = DATASET_DIR / "routing.jsonl"
    rows = read_jsonl(path)
    check_ids(rows, "r", path.name)
    agents = []
    for r in rows:
        a = check_agents(r.get("expected_agents"), f"{path.name} {r.get('id')}")
        if a:
            agents.append(a)
        if r.get("split") not in {"dev", "test"}:
            err(f"{path.name} {r.get('id')}: split must be dev or test")
    check_distribution(agents, path.name)
    splits = Counter(r.get("split") for r in rows)
    amb = sum("ambiguous" in r.get("tags", []) for r in rows)
    print(f"{path.name}: {len(rows)} cases, splits {dict(splits)}, ambiguous {amb}")


def parse_date(value: object, where: str) -> date | None:
    try:
        d = date.fromisoformat(str(value))
    except ValueError:
        err(f"{where}: {value!r} is not YYYY-MM-DD")
        return None
    if not DATE_MIN <= d <= DATE_MAX:
        err(f"{where}: {d} outside {DATE_MIN} to {DATE_MAX}")
    return d


def check_toolcall() -> None:
    path = DATASET_DIR / "toolcall.jsonl"
    rows = read_jsonl(path)
    check_ids(rows, "t", path.name)
    tools = Counter()
    for r in rows:
        where = f"{path.name} {r.get('id')}"
        tool = r.get("expected_tool")
        if tool not in TOOLS:
            err(f"{where}: unknown tool {tool!r}")
            continue
        tools[tool] += 1
        args = r.get("expected_args", {})
        spec = TOOLS[tool]
        missing = spec["required"] - set(args)
        extra = set(args) - spec["required"] - spec["optional"]
        if missing:
            err(f"{where}: missing required args {sorted(missing)}")
        if extra:
            err(f"{where}: unknown args {sorted(extra)}")
        if "date_from" in args and "date_to" in args:
            d0 = parse_date(args["date_from"], where)
            d1 = parse_date(args["date_to"], where)
            if d0 and d1 and d0 > d1:
                err(f"{where}: date_from after date_to")
        if "status" in args and args["status"] not in STATUSES:
            err(f"{where}: unknown status {args['status']!r}")
        if "metric" in args and args["metric"] not in METRICS:
            err(f"{where}: unknown metric {args['metric']!r}")
        for key in ("limit", "min_days_late"):
            if key in args and (not isinstance(args[key], int) or args[key] <= 0):
                err(f"{where}: {key} must be a positive integer")
        if r.get("tool_version") != tool.split("@")[1]:
            err(f"{where}: tool_version does not match the wire name")
    print(f"{path.name}: {len(rows)} cases, tools {dict(tools)}")
    if len(rows) != 40:
        warn(f"{path.name}: {len(rows)} cases, expected 40 (order_lookup needs --orders-csv)")


def main() -> None:
    checks = {"questions": check_questions, "routing": check_routing, "toolcall": check_toolcall}
    if len(sys.argv) != 2 or sys.argv[1] not in checks:
        raise SystemExit(f"usage: validate.py {{{','.join(checks)}}}")
    checks[sys.argv[1]]()
    for w in warnings:
        print(f"WARNING {w}")
    for e in errors:
        print(f"ERROR {e}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
