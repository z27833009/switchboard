"""Feedback harvest selection, filters and deterministic output (AC-24b).

The database itself is exercised in ``tests/integration/test_feedback_integration.py``;
here a fake pool returns canned feedback rows so the selection rule, the SQL
parameters and the file output are tested without PostgreSQL.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import asyncpg

from switchboard.eval.harvest import (
    LATEST_FEEDBACK_SQL,
    candidate_file_name,
    candidates_jsonl,
    harvest,
    parse_since,
    select_candidates,
)

SINCE = datetime(2026, 11, 1, tzinfo=UTC)
RATED = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)


def feedback_row(
    run_id: str,
    *,
    rating: int,
    rated_at: datetime = RATED,
    note: str | None = None,
    tenant_id: str = "default",
    feedback_id: str | None = None,
    query: str = "How many orders were late?",
    answer: str = "1328 orders (t1).",
    plan: str = "data",
    status: str = "ok",
) -> dict[str, Any]:
    return {
        "feedback_id": feedback_id or str(uuid.uuid4()),
        "run_id": run_id,
        "tenant_id": tenant_id,
        "rating": rating,
        "note": note,
        "rated_at": rated_at,
        "query": query,
        "answer": answer,
        "plan": plan,
        "status": status,
        "run_started_at": RATED - timedelta(seconds=8),
        "run_ended_at": RATED - timedelta(seconds=2),
    }


def test_only_negative_ratings_become_candidates() -> None:
    negative = str(uuid.uuid4())
    positive = str(uuid.uuid4())
    rows = [
        feedback_row(negative, rating=-1, note="the count is wrong"),
        feedback_row(positive, rating=1),
    ]

    candidates = select_candidates(rows)

    assert [case["run_id"] for case in candidates] == [negative]
    case = candidates[0]
    assert case["candidate_id"] == f"candidate-{negative}"
    assert case["rating"] == -1
    assert case["note"] == "the count is wrong"
    assert case["query"] == "How many orders were late?"
    assert case["answer"] == "1328 orders (t1)."
    assert case["plan"] == "data"
    assert case["status"] == "ok"
    assert case["tenant_id"] == "default"
    assert case["rated_at"] == RATED.isoformat()
    assert case["run_started_at"] == (RATED - timedelta(seconds=8)).isoformat()
    assert case["run_ended_at"] == (RATED - timedelta(seconds=2)).isoformat()
    # No expected label of any kind: a human fills it in later.
    for key in ("expected_agents", "expected_tool", "expected_args", "split", "labels"):
        assert key not in case


def test_the_newest_rating_is_the_runs_verdict() -> None:
    down_then_up = str(uuid.uuid4())
    up_then_down = str(uuid.uuid4())
    rows = [
        feedback_row(down_then_up, rating=-1, rated_at=RATED),
        feedback_row(down_then_up, rating=1, rated_at=RATED + timedelta(minutes=5)),
        feedback_row(up_then_down, rating=1, rated_at=RATED),
        feedback_row(up_then_down, rating=-1, rated_at=RATED + timedelta(minutes=5)),
    ]

    candidates = select_candidates(rows)

    assert [case["run_id"] for case in candidates] == [up_then_down]


def test_candidate_order_does_not_depend_on_input_order() -> None:
    first = str(uuid.uuid4())
    second = str(uuid.uuid4())
    rows = [
        feedback_row(second, rating=-1, rated_at=RATED + timedelta(minutes=1)),
        feedback_row(first, rating=-1, rated_at=RATED),
    ]

    forwards = candidates_jsonl(select_candidates(rows))
    backwards = candidates_jsonl(select_candidates(list(reversed(rows))))

    assert forwards == backwards
    lines = [json.loads(line) for line in forwards.splitlines()]
    assert [line["run_id"] for line in lines] == [first, second]
    assert forwards.endswith("\n")


def test_empty_selection_writes_no_lines() -> None:
    assert select_candidates([]) == []
    assert candidates_jsonl([]) == ""


def test_since_parsing_and_file_names() -> None:
    assert parse_since(date(2026, 11, 1)) == datetime(2026, 11, 1, tzinfo=UTC)
    assert parse_since(datetime(2026, 11, 1)) == datetime(2026, 11, 1, tzinfo=UTC)
    assert parse_since(datetime(2026, 11, 1, 23, 0, tzinfo=UTC)) == datetime(
        2026, 11, 1, 23, 0, tzinfo=UTC
    )

    assert candidate_file_name(SINCE, None) == "feedback-2026-11-01-all.jsonl"
    assert candidate_file_name(SINCE, "seller_a") == "feedback-2026-11-01-seller_a.jsonl"
    assert candidate_file_name(SINCE, "../evil") == "feedback-2026-11-01-.._evil.jsonl"


class FakePool:
    """Records the SQL arguments and returns canned feedback rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((query, args))
        return [dict(row) for row in self.rows]


async def test_harvest_filters_and_rewrites_the_same_file(tmp_path: Path) -> None:
    negative = str(uuid.uuid4())
    positive = str(uuid.uuid4())
    pool = FakePool(
        [
            feedback_row(negative, rating=-1, note="a policy answer was missing"),
            feedback_row(positive, rating=1),
        ]
    )
    fake = cast(asyncpg.Pool, pool)

    first = await harvest(fake, since=SINCE, tenant_id="default", directory=tmp_path)
    assert first.candidates == 1
    assert first.feedback_rows == 2
    assert first.tenant_id == "default"
    assert first.since == SINCE
    assert first.path == tmp_path / "feedback-2026-11-01-default.jsonl"

    written = first.path.read_text(encoding="utf-8")
    cases = [json.loads(line) for line in written.splitlines()]
    assert [case["run_id"] for case in cases] == [negative]

    # The since moment and the tenant are SQL parameters, not string pastes.
    assert pool.calls[0][0] == LATEST_FEEDBACK_SQL
    assert pool.calls[0][1] == (SINCE, "default")
    assert "created_at >= $1" in LATEST_FEEDBACK_SQL
    assert "tenant_id = $2" in LATEST_FEEDBACK_SQL

    second = await harvest(fake, since=SINCE, tenant_id="default", directory=tmp_path)
    assert second.path.read_text(encoding="utf-8") == written
    assert second.candidates == first.candidates


async def test_harvest_without_a_tenant_passes_null_and_writes_an_empty_file(
    tmp_path: Path,
) -> None:
    pool = FakePool([])
    fake = cast(asyncpg.Pool, pool)

    result = await harvest(fake, since=date(2026, 11, 1), directory=tmp_path)

    assert result.tenant_id is None
    assert result.feedback_rows == 0
    assert result.candidates == 0
    assert pool.calls[0][1] == (datetime(2026, 11, 1, tzinfo=UTC), None)
    assert result.path.name == "feedback-2026-11-01-all.jsonl"
    assert result.path.read_text(encoding="utf-8") == ""
