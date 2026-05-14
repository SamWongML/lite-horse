"""Worker dispatcher routes embed payloads to ``run_embed``.

Doesn't hit Postgres. Just verifies ``is_embed_payload`` shape + the
worker dispatcher hands off to the registered ``embed_fn``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.embed import EmbedMessage, is_embed_payload
from lite_horse.worker.runner import dispatch_message


def test_embed_message_round_trips() -> None:
    msg = EmbedMessage.new(user_id="u", agent_id="a", limit=20)
    raw = msg.to_json()
    assert is_embed_payload(raw) is True
    back = EmbedMessage.from_json(raw)
    assert back == msg


def test_is_embed_payload_rejects_other_kinds() -> None:
    assert is_embed_payload(json.dumps({"kind": "evolve"})) is False
    assert is_embed_payload(json.dumps({"kind": "cron"})) is False
    assert is_embed_payload("not json") is False


@pytest.mark.asyncio
async def test_dispatch_routes_to_embed_handler() -> None:
    captured: list[EmbedMessage] = []

    async def fake_embed(msg: EmbedMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=EmbedMessage.new(user_id="u", agent_id="a").to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, embed_fn=fake_embed)
    assert ok is True
    assert len(captured) == 1
    assert captured[0].user_id == "u"
    assert captured[0].agent_id == "a"


@pytest.mark.asyncio
async def test_dispatch_drops_unparseable_embed_message() -> None:
    raw = QueueMessage(
        body=json.dumps({"kind": "embed", "user_id": "u"}),
        receipt_handle="r-1",
    )

    async def boom(msg: EmbedMessage) -> bool:
        raise AssertionError("should not run on parse failure")

    ok = await dispatch_message(raw, embed_fn=boom)
    # Parse failures count as success so SQS doesn't redeliver poison.
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_handler_exception() -> None:
    async def boom(msg: EmbedMessage) -> bool:
        raise RuntimeError("provider down")

    raw = QueueMessage(
        body=EmbedMessage.new(user_id="u", agent_id="a").to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, embed_fn=boom)
    assert ok is False


def test_embed_payload_does_not_collide_with_evolve_payload() -> None:
    """``is_embed_payload`` and ``is_evolve_payload`` are mutually exclusive."""
    from lite_horse.evolve.cloud import is_evolve_payload

    embed_raw = EmbedMessage.new(user_id="u", agent_id="a").to_json()
    assert is_embed_payload(embed_raw)
    assert not is_evolve_payload(embed_raw)


def test_embed_message_default_limit() -> None:
    msg = EmbedMessage.new(user_id="u", agent_id="a")
    assert msg.limit == 50


_ = Any  # silence unused-import lint when typing isn't needed inline
