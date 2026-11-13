"""JSONL dataset loading (design.md section 20.1).

A dataset version is a directory under ``evals/datasets`` with one JSONL file
per suite. The whole raw record is kept in ``EvalCase.payload`` so a report can
show fields such as ``tool_version`` without a schema change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from switchboard.core.errors import ConfigError
from switchboard.core.models import EvalCase

DEFAULT_DATASET_ROOT = Path("evals/datasets")
SUITE_NAMES = ("routing", "toolcall", "gate", "rag", "injection", "cache")


def dataset_path(suite: str, version: str, root: Path = DEFAULT_DATASET_ROOT) -> Path:
    if not suite:
        raise ConfigError("a suite name is required")
    if not version:
        raise ConfigError("a dataset version is required")
    return Path(root) / version / f"{suite}.jsonl"


def load_cases(suite: str, version: str, root: Path = DEFAULT_DATASET_ROOT) -> list[EvalCase]:
    path = dataset_path(suite, version, root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read dataset {path}: {exc}") from exc

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}:{number}: each line must be a JSON object")
        try:
            case = EvalCase.model_validate({**raw, "payload": raw})
        except ValidationError as exc:
            raise ConfigError(f"{path}:{number}: invalid case: {exc}") from exc
        if case.id in seen:
            raise ConfigError(f"{path}:{number}: duplicate case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)

    if not cases:
        raise ConfigError(f"dataset {path} has no cases")
    return cases


__all__ = ["DEFAULT_DATASET_ROOT", "SUITE_NAMES", "dataset_path", "load_cases"]
