"""Per-message dispatch: parse, run the turn, deliver, ack.

The worker entrypoint long-polls SQS and feeds each :class:`QueueMessage`
into :func:`dispatch_message`. The function is import-light at module
scope; the agent runtime + webhook delivery are pulled in lazily so
unit tests can override both via the ``run_turn_fn`` / ``deliver_fn``
parameters without booting the OpenAI Agents SDK.

A successful dispatch returns ``True`` and the worker deletes the SQS
message; a failure logs and returns ``False``, leaving the message
visible again after its visibility timeout for at-least-once retry.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from lite_horse.cron.scheduler import CronMessage
from lite_horse.evolve.cloud import EvolveMessage, is_evolve_payload, run_evolve
from lite_horse.storage.queue import QueueMessage

log = logging.getLogger(__name__)


@dataclass
class TurnOutcome:
    """The slice of a turn the worker needs after the agent runs."""

    final_text: str
    session_key: str


RunTurnFn = Callable[[CronMessage], Awaitable[TurnOutcome]]
DeliverFn = Callable[[dict[str, Any], str, str], Awaitable[None]]


async def _default_run_turn(msg: CronMessage) -> TurnOutcome:
    """Lazy default: forward to :func:`lite_horse.api.run_turn`.

    The session_key follows the legacy v0.3 cron pattern
    ``"cron-<job_id>-<scheduled_for>"`` so the same job's history
    accumulates predictably.
    """
    from lite_horse import api  # noqa: PLC0415

    session_key = f"cron-{msg.cron_job_id}-{msg.scheduled_for}"
    result = await api.run_turn(
        session_key=session_key,
        user_text=msg.prompt,
        source="cron",
        user_id=msg.user_id,
    )
    return TurnOutcome(final_text=result.final_output, session_key=session_key)


async def _default_deliver(
    spec: dict[str, Any], text: str, session_key: str
) -> None:
    from lite_horse.cron.delivery import deliver_webhook  # noqa: PLC0415

    await deliver_webhook(spec, text, session_key)


async def dispatch_message(
    raw: QueueMessage,
    *,
    run_turn_fn: RunTurnFn | None = None,
    deliver_fn: DeliverFn | None = None,
    evolve_fn: Callable[[EvolveMessage], Awaitable[bool]] | None = None,
) -> bool:
    """Run one queue message end-to-end.

    Returns ``True`` on success (caller should delete the SQS message),
    ``False`` on failure (caller leaves the message for SQS to redeliver).
    Parse failures count as success — a malformed message will never
    parse correctly and shouldn't poison the queue.

    The body's ``kind`` discriminator routes between the cron path
    (default) and the Phase-39 evolve path.
    """
    if is_evolve_payload(raw.body):
        try:
            evolve_msg = EvolveMessage.from_json(raw.body)
        except (ValueError, KeyError, TypeError) as exc:
            log.error("worker: dropping unparseable evolve message: %s", exc)
            return True
        evolve = evolve_fn or run_evolve
        try:
            return await evolve(evolve_msg)
        except Exception:
            log.exception(
                "worker: evolve failed (user=%s slug=%s)",
                evolve_msg.user_id,
                evolve_msg.skill_slug,
            )
            return False

    try:
        msg = CronMessage.from_json(raw.body)
    except (ValueError, KeyError, TypeError) as exc:
        log.error("worker: dropping unparseable message: %s", exc)
        return True

    runner = run_turn_fn or _default_run_turn
    deliver = deliver_fn or _default_deliver

    try:
        outcome = await runner(msg)
    except Exception:
        log.exception(
            "worker: run_turn failed (job=%s user=%s slug=%s)",
            msg.cron_job_id,
            msg.user_id,
            msg.slug,
        )
        return False

    if msg.webhook_url:
        try:
            await deliver(
                {"platform": "webhook", "url": msg.webhook_url},
                outcome.final_text,
                outcome.session_key,
            )
        except Exception:
            log.exception(
                "worker: webhook delivery raised (job=%s)", msg.cron_job_id
            )
            # Webhook failure does NOT requeue the message — the turn
            # already happened (and persisted to the user's session);
            # replaying would double-charge tokens. The webhook layer
            # owns its own retry / dead-letter strategy.
    return True
