"""MessageQueue Protocol — at-least-once message delivery for cron fan-out.

Cloud impl (`queue_sqs.py`) wraps SQS via `aioboto3`. Local impl
(`queue_memory.py`) is an in-process asyncio queue used by tests and
``LITEHORSE_ENV=local`` runs without LocalStack.

The Phase 36 scheduler enqueues; the Phase 36 worker consumes. A queue
message body is a JSON document — see ``cron.scheduler.CronMessage`` for
the canonical shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class QueueMessage:
    """One received message.

    ``receipt_handle`` is opaque; the worker passes it back to
    :meth:`MessageQueue.delete` after the message is processed
    successfully. Failure to delete leaves the message visible again
    after the visibility timeout — that's the at-least-once guarantee.
    """

    body: str
    receipt_handle: str


@runtime_checkable
class MessageQueue(Protocol):
    """At-least-once message queue."""

    async def send(self, body: str) -> None:
        ...

    async def receive(
        self, *, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[QueueMessage]:
        ...

    async def delete(self, receipt_handle: str) -> None:
        ...
