"""Daily audit-archive shipper tick — Phase 46.

Enqueues one :class:`AuditShipMessage` per tick run. The worker handler
selects ``audit_log`` rows older than ``retention_days`` (default 90),
uploads them to the audit-archive bucket as JSONL, and deletes the
shipped rows from Postgres. A single message per day is sufficient
because the worker batches up to ``DEFAULT_BATCH_LIMIT`` rows per run
and the queue redrives until the table is current.
"""
from __future__ import annotations

import logging

from lite_horse.observability import emit_metric
from lite_horse.storage.queue import MessageQueue
from lite_horse.worker.audit_ship import AuditShipMessage

log = logging.getLogger(__name__)


async def audit_ship_tick(*, queue: MessageQueue) -> int:
    """Enqueue one audit-ship batch. Returns the count (always 1)."""
    msg = AuditShipMessage.new()
    await queue.send(msg.to_json())
    log.info("audit_ship tick enqueued 1 message")
    emit_metric("audit_ship_runs_total", 1)
    return 1
