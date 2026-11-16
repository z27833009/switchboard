"""Validate the V3.16 memory candidates before they become a dataset."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ID = re.compile(r"^mem-\d{3}$")
REVIEW = {"pending", "approved"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    errors: list[str] = []
    cases = payload.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be a list")
        cases = []
    if len(cases) != 20:
        errors.append(f"expected 20 cases, got {len(cases)}")

    ids: set[str] = set()
    facts: set[str] = set()
    questions: set[str] = set()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{where} is not an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or ID.fullmatch(case_id) is None:
            errors.append(f"{where}.id must match mem-NNN")
        elif case_id in ids:
            errors.append(f"{where}.id is duplicated: {case_id}")
        else:
            ids.add(case_id)
        fact = case.get("fact")
        question = case.get("question")
        expected = case.get("expected_contains")
        if not isinstance(fact, str) or not fact.strip():
            errors.append(f"{where}.fact must be non-empty")
        elif fact in facts:
            errors.append(f"{where}.fact duplicates another case")
        else:
            facts.add(fact)
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{where}.question must be non-empty")
        elif question in questions:
            errors.append(f"{where}.question duplicates another case")
        else:
            questions.add(question)
        if case.get("review") not in REVIEW:
            errors.append(f"{where}.review must be pending or approved")
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) and item for item in expected)
        ):
            errors.append(f"{where}.expected_contains must be a non-empty string list")
        elif isinstance(question, str):
            for item in expected:
                if item.lower() in question.lower():
                    errors.append(f"{where} leaks expected value in the question: {item}")

    if errors:
        print("\n".join(errors))
        return 1
    approved = sum(1 for case in cases if case.get("review") == "approved")
    print(f"ok: {len(cases)} cases, {approved} approved, {len(cases) - approved} pending")
    return 0


if __name__ == "__main__":
    sys.exit(main())
