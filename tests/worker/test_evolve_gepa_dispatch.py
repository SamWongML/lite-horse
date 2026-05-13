"""Phase 45 — worker dispatcher routes evolve_gepa payloads.

Doesn't hit Postgres. Just verifies :func:`is_evolve_gepa_payload`
shape + the worker dispatcher hands off to the registered
``evolve_gepa_fn``.
"""
from __future__ import annotations

import json

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.gepa import EvolveGepaMessage, is_evolve_gepa_payload
from lite_horse.worker.runner import dispatch_message


def test_evolve_gepa_message_round_trips() -> None:
    msg = EvolveGepaMessage.new(
        user_id="u-1",
        agent_id="a-1",
        skill_slug="demo",
        enqueued_at="2026-05-01T00:00:00+00:00",
    )
    raw = msg.to_json()
    assert is_evolve_gepa_payload(raw) is True
    assert EvolveGepaMessage.from_json(raw) == msg


def test_is_evolve_gepa_payload_rejects_other_kinds() -> None:
    assert is_evolve_gepa_payload(json.dumps({"kind": "evolve"})) is False
    assert is_evolve_gepa_payload(json.dumps({"kind": "embed"})) is False
    assert is_evolve_gepa_payload(json.dumps({"kind": "cron"})) is False
    assert is_evolve_gepa_payload("not json") is False


@pytest.mark.asyncio
async def test_dispatch_routes_to_evolve_gepa_handler() -> None:
    captured: list[EvolveGepaMessage] = []

    async def fake(msg: EvolveGepaMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=EvolveGepaMessage.new(
            user_id="u",
            agent_id="a",
            skill_slug="s",
            enqueued_at="2026-05-01T00:00:00+00:00",
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, evolve_gepa_fn=fake)
    assert ok is True
    assert len(captured) == 1
    assert captured[0].skill_slug == "s"


@pytest.mark.asyncio
async def test_dispatch_drops_unparseable_evolve_gepa_message() -> None:
    raw = QueueMessage(
        body=json.dumps({"kind": "evolve_gepa", "user_id": "u"}),
        receipt_handle="r-1",
    )

    async def boom(_msg: EvolveGepaMessage) -> bool:
        raise AssertionError("should not run on parse failure")

    ok = await dispatch_message(raw, evolve_gepa_fn=boom)
    assert ok is True  # parse failures count as success → SQS deletes


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_handler_exception() -> None:
    async def boom(_msg: EvolveGepaMessage) -> bool:
        raise RuntimeError("provider down")

    raw = QueueMessage(
        body=EvolveGepaMessage.new(
            user_id="u",
            agent_id="a",
            skill_slug="s",
            enqueued_at="2026-05-01T00:00:00+00:00",
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, evolve_gepa_fn=boom)
    assert ok is False
