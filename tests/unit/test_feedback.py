"""Feedback API and rating validation (AC-24a, design.md sections 14 and 20.1).

No PostgreSQL is involved: the app runs on the memory store from ``tests/fakes``.
The append-only grants and the composite foreign key are covered against the
real database in ``tests/integration/test_feedback_integration.py``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError
from tests.fakes import AppHarness

from switchboard.api.schemas import FEEDBACK_NOTE_MAX_CHARS, FeedbackSubmission


async def start_run(harness: AppHarness, tenant_id: str = "default") -> str:
    response = await harness.client.post(
        "/chat", json={"query": f"feedback probe {uuid.uuid4()}", "tenant_id": tenant_id}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["run_id"])


async def post_feedback(
    harness: AppHarness,
    run_id: str,
    *,
    tenant_id: str = "default",
    rating: Any = -1,
    note: Any = None,
) -> Any:
    return await harness.client.post(
        f"/runs/{run_id}/feedback",
        params={"tenant_id": tenant_id},
        json={"rating": rating, "note": note},
    )


def test_rating_must_be_minus_one_or_one() -> None:
    assert FeedbackSubmission(rating=-1).rating == -1
    assert FeedbackSubmission(rating=1).rating == 1
    for rejected in (0, 2, -2, 10, None, "1", "up", True):
        with pytest.raises(ValidationError):
            # The values are deliberately outside the type, like a JSON body.
            FeedbackSubmission.model_validate({"rating": rejected})


def test_note_is_optional_trimmed_and_bounded() -> None:
    assert FeedbackSubmission(rating=1).note is None
    assert FeedbackSubmission(rating=1, note=None).note is None
    assert FeedbackSubmission(rating=1, note="   ").note is None
    assert FeedbackSubmission(rating=-1, note="  count is wrong \n").note == "count is wrong"

    exact = "x" * FEEDBACK_NOTE_MAX_CHARS
    assert FeedbackSubmission(rating=-1, note=exact).note == exact
    with pytest.raises(ValidationError):
        FeedbackSubmission(rating=-1, note=exact + "x")


async def test_feedback_is_stored_for_the_run(harness: AppHarness) -> None:
    run_id = await start_run(harness)
    response = await post_feedback(harness, run_id, note="  The count is wrong.  ")
    assert response.status_code == 201, response.text
    body = response.json()

    assert str(uuid.UUID(body["id"])) == body["id"]
    assert body["run_id"] == run_id
    assert body["tenant_id"] == "default"
    assert body["rating"] == -1
    assert body["note"] == "The count is wrong."
    assert datetime.fromisoformat(body["created_at"]).tzinfo is not None

    stored = harness.store.feedback[-1]
    assert stored["id"] == body["id"]
    assert stored["run_id"] == run_id
    assert stored["tenant_id"] == "default"
    assert stored["rating"] == -1
    assert stored["note"] == "The count is wrong."


async def test_run_detail_shows_the_newest_feedback_and_the_list_does_not(
    harness: AppHarness,
) -> None:
    run_id = await start_run(harness)
    before = await harness.client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
    assert before.status_code == 200
    assert before.json()["latest_feedback"] is None

    first = (await post_feedback(harness, run_id, note="missing the policy")).json()
    detail = await harness.client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
    assert detail.status_code == 200
    assert detail.json()["latest_feedback"]["id"] == first["id"]
    assert detail.json()["latest_feedback"]["rating"] == -1

    # A change of mind appends a row; the newest row is the run's verdict.
    second = (await post_feedback(harness, run_id, rating=1)).json()
    assert len([row for row in harness.store.feedback if row["run_id"] == run_id]) == 2
    detail = await harness.client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
    assert detail.json()["latest_feedback"]["id"] == second["id"]
    assert detail.json()["latest_feedback"]["rating"] == 1

    listed = await harness.client.get("/runs", params={"tenant_id": "default"})
    assert listed.status_code == 200
    row = next(item for item in listed.json()["runs"] if item["run_id"] == run_id)
    assert row["latest_feedback"] is None


async def test_blank_note_is_stored_as_no_note(harness: AppHarness) -> None:
    run_id = await start_run(harness)
    response = await post_feedback(harness, run_id, rating=1, note="   ")
    assert response.status_code == 201
    assert response.json()["note"] is None


async def test_invalid_feedback_is_rejected_before_anything_is_stored(
    harness: AppHarness,
) -> None:
    run_id = await start_run(harness)

    for rejected in (0, 2, -2, "1", "up", None):
        response = await post_feedback(harness, run_id, rating=rejected)
        assert response.status_code == 422, (rejected, response.text)

    too_long = await post_feedback(harness, run_id, note="x" * (FEEDBACK_NOTE_MAX_CHARS + 1))
    assert too_long.status_code == 422

    missing_body = await harness.client.post(
        f"/runs/{run_id}/feedback", params={"tenant_id": "default"}, json={}
    )
    assert missing_body.status_code == 422

    missing_tenant = await harness.client.post(f"/runs/{run_id}/feedback", json={"rating": -1})
    assert missing_tenant.status_code == 422

    no_run = await post_feedback(harness, str(uuid.uuid4()))
    assert no_run.status_code == 404
    malformed = await post_feedback(harness, "not-a-run-id")
    assert malformed.status_code == 404
    unknown_tenant = await post_feedback(harness, run_id, tenant_id="acme")
    assert unknown_tenant.status_code == 404

    assert harness.store.feedback == []


async def test_a_run_of_another_tenant_cannot_be_rated(harness: AppHarness) -> None:
    run_id = await start_run(harness)

    cross = await post_feedback(harness, run_id, tenant_id="seller_a")
    assert cross.status_code == 404
    assert cross.json()["detail"] == "run not found"
    assert harness.store.feedback == []

    # The run and its feedback stay invisible to the other tenant.
    other = await harness.client.get(f"/runs/{run_id}", params={"tenant_id": "seller_a"})
    assert other.status_code == 404
    no_tenant = await harness.client.get(f"/runs/{run_id}")
    assert no_tenant.status_code == 422


async def test_feedback_has_no_edit_and_no_delete(harness: AppHarness) -> None:
    run_id = await start_run(harness)
    created = (await post_feedback(harness, run_id)).json()
    url = f"/runs/{run_id}/feedback"

    for method in ("PUT", "PATCH", "DELETE"):
        response = await harness.client.request(
            method,
            url,
            params={"tenant_id": "default"},
            json={"rating": 1},
        )
        assert response.status_code == 405, (method, response.status_code)

    stored = await harness.client.get(f"/runs/{run_id}", params={"tenant_id": "default"})
    assert stored.json()["latest_feedback"]["id"] == created["id"]
    assert stored.json()["latest_feedback"]["rating"] == -1
