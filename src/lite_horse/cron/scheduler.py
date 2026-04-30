"""APScheduler wiring + delivery for scheduled cron jobs.

One ``AsyncIOScheduler`` runs on the main event loop; each firing builds a
fresh agent + session (source ``"cron"``) and hands the final output to a
delivery handler. Phase 15 dropped Telegram delivery; Phase 17 wires the
webhook handler so cron output reaches the embedding webapp. Shutdown is
signal-driven — SIGINT / SIGTERM stop the scheduler and remove ``cron.pid``.

Phase 36 adds the fan-out shape (:class:`CronMessage` +
:func:`expand_official_to_user_messages`) consumed by the cloud
``scheduler/`` and ``worker/`` services. The legacy in-process path
above stays for the v0.3 single-user CLI; the cloud path enqueues
:class:`CronMessage` JSON onto SQS and drains it from the worker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from agents import Runner
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lite_horse.agent.errors import ErrorKind, classify
from lite_horse.config import Config, load_config
from lite_horse.constants import litehorse_home
from lite_horse.cron.delivery import deliver_webhook
from lite_horse.cron.jobs import Job, JobStore
from lite_horse.sessions.local import LocalSessionRepo
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db

log = logging.getLogger(__name__)

Fire = Callable[[Job], Awaitable[None]]
Deliver = Callable[[dict[str, Any], str, str], Awaitable[None]]

# Three consecutive MODEL_REFUSAL firings and the job is auto-disabled with a
# reason stamped into the JobStore. Strikes are in-memory (per scheduler
# process) and reset on any successful firing.
_REFUSAL_STRIKE_LIMIT = 3

_ALIASES: dict[str, str] = {
    "@minutely": "* * * * *",
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@weekly": "0 0 * * 0",
}


def parse_schedule(schedule: str) -> CronTrigger:
    """Turn a schedule string into an APScheduler ``CronTrigger``.

    Raises ``ValueError`` on unknown aliases or malformed crontab expressions.
    APScheduler picks the system local timezone when none is supplied.
    """
    s = schedule.strip()
    if s.startswith("@"):
        if s not in _ALIASES:
            raise ValueError(f"unknown schedule alias: {s!r}")
        s = _ALIASES[s]
    return CronTrigger.from_crontab(s)


async def deliver_log(
    _spec: dict[str, Any], text: str, _session_key: str
) -> None:
    log.info("[cron output] %s", text)


DELIVERY_HANDLERS: dict[str, Deliver] = {
    "log": deliver_log,
    "webhook": deliver_webhook,
}


async def deliver(spec: dict[str, Any], text: str, session_key: str) -> None:
    platform = spec.get("platform", "log")
    handler = DELIVERY_HANDLERS.get(platform)
    if handler is None:
        log.error("unknown delivery platform: %s", platform)
        return
    await handler(spec, text, session_key)


def make_fire(
    *, db: LocalSessionRepo, cfg: Config, store: JobStore | None = None
) -> Fire:
    """Build the closure APScheduler calls when a job is due.

    Exposed so tests can invoke a firing directly without standing up a real
    scheduler. ``build_agent`` is imported lazily to avoid a cron ↔ factory
    import cycle (``factory`` pulls in ``cron_manage``).

    ``store`` enables the Phase-22 strike-based auto-disable. When None
    (older callers, tests that only exercise the happy path), refusals are
    still classified and logged but never disable the job.
    """
    from lite_horse.agent.factory import build_agent  # noqa: PLC0415

    refusal_strikes: dict[str, int] = {}

    async def fire(job: Job) -> None:
        log.info("cron firing: %s", job.id)
        sid = f"cron-{job.id}-{int(time.time())}"
        session = SDKSession(sid, db, source="cron")
        agent = build_agent(config=cfg)
        try:
            result = await Runner.run(
                agent,
                job.prompt,
                session=session,  # type: ignore[arg-type]
                max_turns=cfg.agent.max_turns,
            )
            refusal_strikes.pop(job.id, None)
            await deliver(job.delivery, str(result.final_output), sid)
        except Exception as exc:
            classified = classify(exc)
            log.error(
                "cron job %s failed (%s): %s",
                job.id,
                classified.kind,
                classified.summary,
            )
            if classified.kind is ErrorKind.MODEL_REFUSAL:
                strikes = refusal_strikes.get(job.id, 0) + 1
                refusal_strikes[job.id] = strikes
                if store is not None and strikes >= _REFUSAL_STRIKE_LIMIT:
                    store.disable_with_reason(
                        job.id, f"auto-disabled: {strikes} model refusals"
                    )
                    refusal_strikes.pop(job.id, None)
                    log.warning(
                        "cron job %s auto-disabled after %d refusals",
                        job.id,
                        strikes,
                    )
            else:
                refusal_strikes.pop(job.id, None)
            await deliver(
                job.delivery,
                f"⚠ cron job {job.id} failed ({classified.kind}): "
                f"{classified.summary}",
                sid,
            )
        finally:
            db.end_session(sid, end_reason="cron_done")

    return fire


def _schedule_jobs(
    sched: AsyncIOScheduler, store: JobStore, fire: Fire
) -> int:
    """Register every enabled job on the scheduler. Returns the count loaded."""
    loaded = 0
    for job in store.all():
        if not job.enabled:
            continue
        try:
            trig = parse_schedule(job.schedule)
        except (ValueError, KeyError) as e:
            log.error("invalid schedule %r for job %s: %s", job.schedule, job.id, e)
            continue
        sched.add_job(fire, trig, args=[job], id=job.id, replace_existing=True)
        loaded += 1
    return loaded


async def run_scheduler() -> None:
    """Async entrypoint: load jobs, start the scheduler, wait for SIGINT/SIGTERM."""
    cfg = load_config()
    db = LocalSessionRepo()
    bind_db(db)
    store = JobStore()
    sched = AsyncIOScheduler()

    fire = make_fire(db=db, cfg=cfg, store=store)
    loaded = _schedule_jobs(sched, store, fire)

    # Install signal handlers BEFORE advertising readiness via the pid
    # file: the subprocess test (and supervisors in general) read the
    # pid file as the "scheduler is up, you can send signals now" mark,
    # so any SIGTERM landing before the handler is registered would
    # kill the process with rc=-15 instead of triggering clean shutdown.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows / restricted envs — fall back to KeyboardInterrupt.
            pass

    home = litehorse_home()
    home.mkdir(parents=True, exist_ok=True)
    pid_file = home / "cron.pid"
    pid_file.write_text(str(os.getpid()))

    sched.start()
    log.info("cron up; %d jobs loaded", loaded)

    try:
        await stop.wait()
    finally:
        sched.shutdown(wait=False)
        if pid_file.exists():
            pid_file.unlink()


def run_scheduler_blocking() -> None:
    """CLI entrypoint: block the current thread until a shutdown signal."""
    asyncio.run(run_scheduler())


# ---------------------------------------------------------------------------
# Phase 36 cloud fan-out
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CronMessage:
    """One queue message produced by a scheduler tick.

    The body of an SQS message is ``CronMessage.to_json()``. The worker
    parses with :meth:`from_json` and dispatches the turn under the
    target user's tenant. ``scheduled_for`` is the wall-clock the
    scheduler decided this fire should represent (ISO-8601 UTC) — not
    when SQS or the worker actually got the message.

    ``cron_job_id`` is the row id in ``cron_jobs``. For official-scope
    jobs the same id appears on every fan-out message; the ``user_id``
    differs per recipient. ``scope`` is mirrored from the row so the
    worker doesn't need to round-trip the DB to learn it.
    """

    cron_job_id: str
    scope: str
    slug: str
    user_id: str
    prompt: str
    webhook_url: str | None
    scheduled_for: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> CronMessage:
        data = json.loads(raw)
        return cls(
            cron_job_id=str(data["cron_job_id"]),
            scope=str(data["scope"]),
            slug=str(data["slug"]),
            user_id=str(data["user_id"]),
            prompt=str(data["prompt"]),
            webhook_url=data.get("webhook_url"),
            scheduled_for=str(data["scheduled_for"]),
        )


def expand_official_to_user_messages(
    *,
    cron_job_id: str,
    slug: str,
    prompt: str,
    webhook_url: str | None,
    active_user_ids: list[str],
    scheduled_for: str,
) -> list[CronMessage]:
    """Fan one official-scope job out to one message per active user.

    Returns an empty list if there are no active users — the scheduler
    still marks the job as fired so it won't try again until the next
    cron expression boundary.
    """
    return [
        CronMessage(
            cron_job_id=cron_job_id,
            scope="official",
            slug=slug,
            user_id=user_id,
            prompt=prompt,
            webhook_url=webhook_url,
            scheduled_for=scheduled_for,
        )
        for user_id in active_user_ids
    ]


def build_user_message(
    *,
    cron_job_id: str,
    slug: str,
    user_id: str,
    prompt: str,
    webhook_url: str | None,
    scheduled_for: str,
) -> CronMessage:
    """Build the single message a user-scope job produces per fire."""
    return CronMessage(
        cron_job_id=cron_job_id,
        scope="user",
        slug=slug,
        user_id=user_id,
        prompt=prompt,
        webhook_url=webhook_url,
        scheduled_for=scheduled_for,
    )


def is_due(*, cron_expr: str, last_fired_at: Any, now: Any) -> bool:
    """Return True if a cron row's next-fire from ``last_fired_at`` is ≤ ``now``.

    ``last_fired_at`` may be ``None`` (never fired) — in which case any
    fire boundary on or before ``now`` qualifies as due. Rejects
    schedules with no next fire from the reference point (raises so the
    caller can disable the job).
    """
    trigger = parse_schedule(cron_expr)
    next_fire = trigger.get_next_fire_time(last_fired_at, now)
    if next_fire is None:
        raise ValueError(f"cron expression {cron_expr!r} produced no next fire")
    return bool(next_fire <= now)
