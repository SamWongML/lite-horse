"""Worker service entrypoint — ``python -m lite_horse.worker``.

Long-polls SQS in a loop; for each batch, dispatches every message
through :func:`lite_horse.worker.runner.dispatch_message` and deletes
successful ones. Failures stay visible and SQS redrives them.

Concurrency: messages within a batch are processed in parallel via
:func:`asyncio.gather` so a slow turn doesn't block its peers. Across
batches the loop is sequential — autoscaling is the way to add
throughput, not in-process concurrency.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from lite_horse.config import get_settings
from lite_horse.storage import make_message_queue
from lite_horse.storage.queue import MessageQueue, QueueMessage
from lite_horse.worker.runner import dispatch_message

POLL_BATCH = 10
POLL_WAIT_SECONDS = 20

log = logging.getLogger(__name__)


async def _process_one(queue: MessageQueue, raw: QueueMessage) -> None:
    ok = await dispatch_message(raw)
    if ok:
        try:
            await queue.delete(raw.receipt_handle)
        except Exception:
            log.exception("worker: SQS delete raised")


async def run_worker(*, stop: asyncio.Event) -> None:
    """Drain the queue until ``stop`` is set."""
    settings = get_settings()
    log.info("worker boot env=%s", settings.env)
    queue = make_message_queue()
    while not stop.is_set():
        try:
            batch = await queue.receive(
                max_messages=POLL_BATCH, wait_seconds=POLL_WAIT_SECONDS
            )
        except Exception:
            log.exception("worker: receive raised; backing off 5s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except TimeoutError:
                pass
            continue
        if not batch:
            continue
        await asyncio.gather(
            *(_process_one(queue, raw) for raw in batch), return_exceptions=False
        )
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    stop = asyncio.Event()

    async def _runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        await run_worker(stop=stop)

    asyncio.run(_runner())


if __name__ == "__main__":
    main()
