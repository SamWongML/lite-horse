"""Worker dispatcher routes classify + curate payloads."""
from __future__ import annotations

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.classify import ClassifyMessage, is_classify_payload
from lite_horse.worker.curate import CurateMessage, is_curate_payload
from lite_horse.worker.runner import dispatch_message


def test_classify_message_round_trips() -> None:
    msg = ClassifyMessage.new(
        user_id="u",
        agent_id="a",
        session_id="s",
        turn_id="t",
        user_request="ask",
        final_text="answer",
        tool_tail=[{"name": "x", "output": "ok"}],
        skill_slug="slug",
    )
    raw = msg.to_json()
    assert is_classify_payload(raw) is True
    back = ClassifyMessage.from_json(raw)
    assert back.turn_id == "t"
    assert back.skill_slug == "slug"


def test_curate_message_round_trips() -> None:
    msg = CurateMessage.new(user_id="u", agent_id="a")
    raw = msg.to_json()
    assert is_curate_payload(raw) is True
    back = CurateMessage.from_json(raw)
    assert back == msg


def test_kind_discriminators_are_mutually_exclusive() -> None:
    cls_raw = ClassifyMessage.new(
        user_id="u", agent_id="a", session_id="s", turn_id="t"
    ).to_json()
    cur_raw = CurateMessage.new(user_id="u", agent_id="a").to_json()
    assert is_classify_payload(cls_raw) and not is_curate_payload(cls_raw)
    assert is_curate_payload(cur_raw) and not is_classify_payload(cur_raw)


@pytest.mark.asyncio
async def test_dispatch_routes_to_classify_handler() -> None:
    captured: list[ClassifyMessage] = []

    async def fake_classify(msg: ClassifyMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=ClassifyMessage.new(
            user_id="u", agent_id="a", session_id="s", turn_id="t"
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, classify_fn=fake_classify)
    assert ok is True
    assert captured[0].turn_id == "t"


@pytest.mark.asyncio
async def test_dispatch_routes_to_curate_handler() -> None:
    captured: list[CurateMessage] = []

    async def fake_curate(msg: CurateMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=CurateMessage.new(user_id="u", agent_id="a").to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, curate_fn=fake_curate)
    assert ok is True
    assert captured[0].agent_id == "a"


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_classify_exception() -> None:
    async def boom(msg: ClassifyMessage) -> bool:
        raise RuntimeError("classifier upstream down")

    raw = QueueMessage(
        body=ClassifyMessage.new(
            user_id="u", agent_id="a", session_id="s", turn_id="t"
        ).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, classify_fn=boom)
    assert ok is False


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_curate_exception() -> None:
    async def boom(msg: CurateMessage) -> bool:
        raise RuntimeError("curator upstream down")

    raw = QueueMessage(
        body=CurateMessage.new(user_id="u", agent_id="a").to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, curate_fn=boom)
    assert ok is False
