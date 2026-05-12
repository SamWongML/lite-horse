"""Phase 43 — worker dispatcher routes summarize/compact payloads."""
from __future__ import annotations

import json

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.compact import CompactMessage, is_compact_payload
from lite_horse.worker.runner import dispatch_message
from lite_horse.worker.summarize import SummarizeMessage, is_summarize_payload


def test_summarize_message_round_trips() -> None:
    msg = SummarizeMessage.new(
        user_id="u", agent_id="a", session_id="s", model="gpt-5.4-mini"
    )
    raw = msg.to_json()
    assert is_summarize_payload(raw) is True
    back = SummarizeMessage.from_json(raw)
    assert back == msg


def test_compact_message_round_trips() -> None:
    msg = CompactMessage.new(user_id="u", agent_id="a")
    raw = msg.to_json()
    assert is_compact_payload(raw) is True
    back = CompactMessage.from_json(raw)
    assert back == msg


def test_kind_discriminators_are_mutually_exclusive() -> None:
    s_raw = SummarizeMessage.new(
        user_id="u", agent_id="a", session_id="s"
    ).to_json()
    c_raw = CompactMessage.new(user_id="u", agent_id="a").to_json()
    assert is_summarize_payload(s_raw) and not is_compact_payload(s_raw)
    assert is_compact_payload(c_raw) and not is_summarize_payload(c_raw)


@pytest.mark.asyncio
async def test_dispatch_routes_to_summarize_handler() -> None:
    captured: list[SummarizeMessage] = []

    async def fake_summarize(msg: SummarizeMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=SummarizeMessage.new(
            user_id="u", agent_id="a", session_id="s"
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, summarize_fn=fake_summarize)
    assert ok is True
    assert captured[0].session_id == "s"


@pytest.mark.asyncio
async def test_dispatch_routes_to_compact_handler() -> None:
    captured: list[CompactMessage] = []

    async def fake_compact(msg: CompactMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=CompactMessage.new(user_id="u", agent_id="a").to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, compact_fn=fake_compact)
    assert ok is True
    assert captured[0].user_id == "u"


@pytest.mark.asyncio
async def test_dispatch_drops_unparseable_summarize_message() -> None:
    async def boom(msg: SummarizeMessage) -> bool:
        raise AssertionError("should not run on parse failure")

    raw = QueueMessage(
        body=json.dumps({"kind": "summarize", "user_id": "u"}),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, summarize_fn=boom)
    assert ok is True


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_summarize_exception() -> None:
    async def boom(msg: SummarizeMessage) -> bool:
        raise RuntimeError("provider down")

    raw = QueueMessage(
        body=SummarizeMessage.new(
            user_id="u", agent_id="a", session_id="s"
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, summarize_fn=boom)
    assert ok is False
