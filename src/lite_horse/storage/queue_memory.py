"""In-memory MessageQueue — local dev + unit tests.

Single-process only. Receipt handles are unique tokens; ``delete`` is
idempotent. ``receive`` blocks up to ``wait_seconds`` waiting for a
message, mirroring SQS long-polling semantics.
"""
from __future__ import annotations

import asyncio
import secrets

from lite_horse.storage.queue import MessageQueue, QueueMessage


class InMemoryMessageQueue(MessageQueue):
    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueueMessage] = asyncio.Queue()
        self._inflight: set[str] = set()

    async def send(self, body: str) -> None:
        msg = QueueMessage(body=body, receipt_handle=secrets.token_hex(8))
        await self._queue.put(msg)

    async def receive(
        self, *, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[QueueMessage]:
        out: list[QueueMessage] = []
        if wait_seconds <= 0:
            # Non-blocking drain — useful for tests that send N messages
            # and immediately read them back.
            while len(out) < max_messages:
                try:
                    msg = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                out.append(msg)
                self._inflight.add(msg.receipt_handle)
            return out
        try:
            first = await asyncio.wait_for(self._queue.get(), timeout=wait_seconds)
        except TimeoutError:
            return out
        out.append(first)
        self._inflight.add(first.receipt_handle)
        while len(out) < max_messages:
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            out.append(msg)
            self._inflight.add(msg.receipt_handle)
        return out

    async def delete(self, receipt_handle: str) -> None:
        self._inflight.discard(receipt_handle)

    # Test helpers --------------------------------------------------

    def qsize(self) -> int:
        return self._queue.qsize()

    def inflight(self) -> int:
        return len(self._inflight)
