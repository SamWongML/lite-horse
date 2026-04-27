"""Worker dispatch tests — pure (no DB, no SQS, no agent runtime).

The worker's runner is decoupled from the agent SDK and the cloud SQS
client by parameterising :func:`dispatch_message` over ``run_turn_fn``
and ``deliver_fn``. Tests inject deterministic stubs for both.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lite_horse.cron.scheduler import CronMessage, build_user_message
from lite_horse.storage.queue import QueueMessage
from lite_horse.storage.queue_memory import InMemoryMessageQueue
from lite_horse.worker.runner import TurnOutcome, dispatch_message

pytestmark = pytest.mark.asyncio


def _msg(**overrides: Any) -> CronMessage:
    base = build_user_message(
        cron_job_id="job-1",
        slug="standup",
        user_id="user-1",
        prompt="run the thing",
        webhook_url="https://hook/standup",
        scheduled_for="2026-04-28T09:00:00+00:00",
    )
    if not overrides:
        return base
    fields = {**base.__dict__, **overrides}
    return CronMessage(**fields)


async def test_dispatch_runs_turn_and_delivers() -> None:
    captured: dict[str, Any] = {}

    async def run_turn_fn(msg: CronMessage) -> TurnOutcome:
        captured["msg"] = msg
        return TurnOutcome(final_text="hello", session_key="cron-job-1-now")

    async def deliver_fn(spec: dict[str, Any], text: str, sk: str) -> None:
        captured["deliver"] = (spec, text, sk)

    raw = QueueMessage(body=_msg().to_json(), receipt_handle="r1")
    ok = await dispatch_message(raw, run_turn_fn=run_turn_fn, deliver_fn=deliver_fn)
    assert ok is True
    assert captured["msg"].user_id == "user-1"
    assert captured["deliver"][0]["url"] == "https://hook/standup"
    assert captured["deliver"][1] == "hello"


async def test_dispatch_skips_delivery_when_no_webhook() -> None:
    msg = _msg(webhook_url=None)
    delivered = False

    async def run_turn_fn(_: CronMessage) -> TurnOutcome:
        return TurnOutcome(final_text="x", session_key="sk")

    async def deliver_fn(*_a: Any) -> None:  # pragma: no cover — must not run
        nonlocal delivered
        delivered = True

    ok = await dispatch_message(
        QueueMessage(body=msg.to_json(), receipt_handle="r"),
        run_turn_fn=run_turn_fn,
        deliver_fn=deliver_fn,
    )
    assert ok is True
    assert delivered is False


async def test_dispatch_returns_false_on_run_turn_failure() -> None:
    async def run_turn_fn(_: CronMessage) -> TurnOutcome:
        raise RuntimeError("model exploded")

    async def deliver_fn(*_a: Any) -> None:  # pragma: no cover
        raise AssertionError("should not run")

    ok = await dispatch_message(
        QueueMessage(body=_msg().to_json(), receipt_handle="r"),
        run_turn_fn=run_turn_fn,
        deliver_fn=deliver_fn,
    )
    assert ok is False


async def test_dispatch_drops_unparseable_message() -> None:
    async def run_turn_fn(_: CronMessage) -> TurnOutcome:  # pragma: no cover
        raise AssertionError("should not run")

    async def deliver_fn(*_a: Any) -> None:  # pragma: no cover
        raise AssertionError("should not run")

    ok = await dispatch_message(
        QueueMessage(body="this is not json", receipt_handle="r"),
        run_turn_fn=run_turn_fn,
        deliver_fn=deliver_fn,
    )
    # Unparseable poison messages count as success so they're deleted
    # (otherwise SQS would redeliver them forever).
    assert ok is True


async def test_dispatch_swallows_webhook_failure() -> None:
    async def run_turn_fn(_: CronMessage) -> TurnOutcome:
        return TurnOutcome(final_text="x", session_key="sk")

    async def deliver_fn(*_a: Any) -> None:
        raise httpx_failure("kaboom")

    ok = await dispatch_message(
        QueueMessage(body=_msg().to_json(), receipt_handle="r"),
        run_turn_fn=run_turn_fn,
        deliver_fn=deliver_fn,
    )
    # Webhook failure does NOT requeue — the turn already happened and
    # replay would double-charge tokens.
    assert ok is True


async def test_in_memory_queue_round_trip() -> None:
    """End-to-end sanity: send → receive → delete leaves the queue empty."""
    q = InMemoryMessageQueue()
    await q.send("payload-1")
    await q.send("payload-2")
    received = await asyncio.wait_for(
        q.receive(max_messages=10, wait_seconds=1), timeout=2.0
    )
    assert {m.body for m in received} == {"payload-1", "payload-2"}
    for m in received:
        await q.delete(m.receipt_handle)
    assert q.qsize() == 0
    assert q.inflight() == 0


# Helper at module scope — async helpers in tests can't define a
# closure-only exception class without confusing mypy.
def httpx_failure(reason: str) -> Exception:
    return RuntimeError(reason)
