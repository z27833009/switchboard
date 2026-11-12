"""Feedback harvest: negatively rated runs become unlabeled candidate cases.

``switchboard eval harvest --since <date> [--tenant <id>]`` reads the feedback
users left on runs and writes the negatively rated ones to
``evals/candidates/`` as unlabeled JSONL (design.md sections 14 and 20.1).

Rules:

- A run's verdict is its newest feedback row. A run rated down and then up is
  not a candidate, and a run rated up and then down is.
- Only rating ``-1`` is exported. A candidate carries no expected label:
  ``query``, ``answer``, ``plan``, ``status``, ``rating``, ``note``, ``run_id``
  and timestamps, and nothing that says what the right answer would be.
- ``--since`` filters on when the feedback was written, in UTC: the command
  harvests ratings received from that day on, not runs that started then.
- One file per since date and tenant: ``feedback-<since>-<tenant|all>.jsonl``.
  The file is rewritten from the current rows on every run, so running the
  command twice writes the same bytes and never appends a duplicate case.
- A candidate is a proposal for the next dataset version. It never changes a
  frozen dataset or a baseline; a human labels candidates and the labeled cases
  enter a new version (``evals/datasets/README.md`` and design 20.1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import asyncpg

from switchboard.core.errors import ConfigError

DEFAULT_CANDIDATE_DIR = Path("evals/candidates")
NEGATIVE_RATING = -1
CANDIDATE_ID_PREFIX = "candidate-"

# One feedback row per run: the newest, because that is the run's verdict.
# ``$1`` is the since timestamp and ``$2`` the tenant or null for all tenants,
# so both filters run in SQL. The join carries the tenant as well, and the
# feedback table's composite foreign key already forbids a cross-tenant pair
# (05-feedback.sql). The negative-rating rule is applied by
# ``select_candidates`` so it is one tested rule and not a second WHERE clause.
LATEST_FEEDBACK_SQL = """
SELECT f.id AS feedback_id,
       f.run_id,
       f.tenant_id,
       f.rating,
       f.note,
       f.created_at AS rated_at,
       r.query,
       r.answer,
       r.plan,
       r.status,
       r.started_at AS run_started_at,
       r.ended_at AS run_ended_at
FROM (
    SELECT DISTINCT ON (run_id) id, run_id, tenant_id, rating, note, created_at
    FROM public.feedback
    WHERE created_at >= $1
      AND ($2::text IS NULL OR tenant_id = $2)
    ORDER BY run_id, created_at DESC, id DESC
) AS f
JOIN public.runs AS r ON r.id = f.run_id AND r.tenant_id = f.tenant_id
ORDER BY f.created_at, f.run_id
"""

_UNSAFE_FILE_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True)
class HarvestResult:
    """What one harvest wrote, for the CLI to print and a test to assert."""

    path: Path
    since: datetime
    tenant_id: str | None
    feedback_rows: int
    candidates: int


def parse_since(value: datetime | date) -> datetime:
    """Midnight UTC of the date, or the moment itself when a time is given."""
    # A date is not a datetime in Python, so the isinstance check is required.
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def candidate_id(run_id: str) -> str:
    """A stable id, so a labeled case can be traced back to its run."""
    return f"{CANDIDATE_ID_PREFIX}{run_id}"


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


def candidate_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One unlabeled case. No ``expected_agents`` and no ``expected_tool``."""
    run_id = str(row["run_id"])
    return {
        "candidate_id": candidate_id(run_id),
        "run_id": run_id,
        "tenant_id": str(row["tenant_id"]),
        "query": str(row.get("query") or ""),
        "answer": cast(str | None, row.get("answer")),
        "plan": cast(str | None, row.get("plan")),
        "status": str(row.get("status") or ""),
        "rating": int(row["rating"]),
        "note": cast(str | None, row.get("note")),
        "run_started_at": _isoformat(row.get("run_started_at")),
        "run_ended_at": _isoformat(row.get("run_ended_at")),
        "rated_at": _isoformat(row.get("rated_at")),
    }


def select_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Newest feedback row per run, negative ratings only, stable order.

    The rule lives here rather than only in SQL so a unit test can feed mixed
    ratings, repeated runs and unsorted rows and check the outcome.
    """
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        run_id = str(row["run_id"])
        current = latest.get(run_id)
        if current is None or _order_key(row) > _order_key(current):
            latest[run_id] = row
    candidates = [
        candidate_from_row(row) for row in latest.values() if int(row["rating"]) == NEGATIVE_RATING
    ]
    candidates.sort(key=lambda case: (cast(str, case["rated_at"]), cast(str, case["run_id"])))
    return candidates


def _order_key(row: Mapping[str, Any]) -> tuple[str, str]:
    rated_at = _isoformat(row.get("rated_at")) or ""
    return (rated_at, str(row.get("feedback_id") or ""))


def candidates_jsonl(candidates: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic JSONL: sorted keys, one line per case, trailing newline."""
    if not candidates:
        return ""
    lines = [json.dumps(case, ensure_ascii=False, sort_keys=True) for case in candidates]
    return "\n".join(lines) + "\n"


def candidate_file_name(since: datetime, tenant_id: str | None) -> str:
    """``feedback-<since>-<tenant|all>.jsonl``; the tenant is file-name safe."""
    tenant = _UNSAFE_FILE_CHARS.sub("_", tenant_id) if tenant_id else "all"
    return f"feedback-{parse_since(since).date().isoformat()}-{tenant}.jsonl"


def write_candidates(path: Path, text: str) -> None:
    """Write through a temporary file so a failed run leaves the old one intact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


async def harvest(
    pool: asyncpg.Pool,
    *,
    since: datetime | date,
    tenant_id: str | None = None,
    directory: Path = DEFAULT_CANDIDATE_DIR,
) -> HarvestResult:
    """Read feedback from the database and write one candidate file."""
    moment = parse_since(since)
    rows = [dict(row) for row in await pool.fetch(LATEST_FEEDBACK_SQL, moment, tenant_id)]
    candidates = select_candidates(rows)
    path = Path(directory) / candidate_file_name(moment, tenant_id)
    write_candidates(path, candidates_jsonl(candidates))
    return HarvestResult(
        path=path,
        since=moment,
        tenant_id=tenant_id,
        feedback_rows=len(rows),
        candidates=len(candidates),
    )


def require_database_url(url: str) -> str:
    """The harvest reads through the application role, which can read feedback."""
    if not url:
        raise ConfigError("set SWITCHBOARD_DATABASE_URL or pass --database-url")
    return url


__all__ = [
    "CANDIDATE_ID_PREFIX",
    "DEFAULT_CANDIDATE_DIR",
    "LATEST_FEEDBACK_SQL",
    "NEGATIVE_RATING",
    "HarvestResult",
    "candidate_file_name",
    "candidate_from_row",
    "candidate_id",
    "candidates_jsonl",
    "harvest",
    "parse_since",
    "require_database_url",
    "select_candidates",
    "write_candidates",
]
