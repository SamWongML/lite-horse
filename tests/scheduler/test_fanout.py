"""Pure-unit tests for the fan-out helpers in :mod:`lite_horse.cron.scheduler`.

No DB / no SQS: tests the message-shape building blocks the scheduler
tick assembles. Live integration is covered in
``test_tick_integration.py`` (testcontainers).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from lite_horse.cron.scheduler import (
    CronMessage,
    build_user_message,
    expand_official_to_user_messages,
    is_due,
)


def test_user_message_round_trip() -> None:
    msg = build_user_message(
        cron_job_id="job-1",
        slug="standup",
        user_id="user-1",
        prompt="post the standup",
        webhook_url="https://example.com/hook",
        scheduled_for="2026-04-28T09:00:00+00:00",
    )
    raw = msg.to_json()
    parsed = json.loads(raw)
    assert parsed["scope"] == "user"
    assert parsed["user_id"] == "user-1"
    again = CronMessage.from_json(raw)
    assert again == msg


def test_official_fans_out_one_per_user() -> None:
    users = ["u1", "u2", "u3"]
    messages = expand_official_to_user_messages(
        cron_job_id="job-9",
        slug="weekly-newsletter",
        prompt="run the newsletter",
        webhook_url=None,
        active_user_ids=users,
        scheduled_for="2026-04-28T09:00:00+00:00",
    )
    assert [m.user_id for m in messages] == users
    assert all(m.scope == "official" for m in messages)
    assert all(m.cron_job_id == "job-9" for m in messages)
    assert all(m.webhook_url is None for m in messages)


def test_official_with_no_active_users_returns_empty() -> None:
    messages = expand_official_to_user_messages(
        cron_job_id="job-9",
        slug="weekly-newsletter",
        prompt="hello",
        webhook_url=None,
        active_user_ids=[],
        scheduled_for="2026-04-28T09:00:00+00:00",
    )
    assert messages == []


def test_is_due_when_never_fired() -> None:
    now = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)
    # Cron `* * * * *` always has a next fire boundary at or before now.
    assert is_due(cron_expr="* * * * *", last_fired_at=None, now=now)


def test_is_due_after_window_boundary() -> None:
    last = datetime(2026, 4, 28, 8, 0, tzinfo=UTC)
    # Hourly job fired at 08:00 — at 09:00 the next 09:00 boundary is due.
    now = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)
    assert is_due(cron_expr="0 * * * *", last_fired_at=last, now=now)


def test_is_due_inside_window_returns_false() -> None:
    last = datetime(2026, 4, 28, 9, 0, tzinfo=UTC)
    # Hourly job fired at 09:00 — at 09:30 the next boundary 10:00 is in
    # the future, so not due.
    now = last + timedelta(minutes=30)
    assert not is_due(cron_expr="0 * * * *", last_fired_at=last, now=now)


def test_is_due_invalid_expression_raises() -> None:
    with pytest.raises(ValueError):
        is_due(cron_expr="not a cron", last_fired_at=None, now=datetime.now(UTC))
