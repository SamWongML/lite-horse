"""APScheduler wiring + delivery for scheduled cron jobs.

One ``AsyncIOScheduler`` runs on the main event loop; each firing builds a
fresh agent + session (source ``"cron"``) and hands the final output to a
delivery handler. Phase 15 dropped Telegram delivery; Phase 17 wires the
webhook handler so cron output reaches the embedding webapp. Shutdown is
signal-driven — SIGINT / SIGTERM stop the scheduler and remove ``cron.pid``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Awaitable, Callable
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

    home = litehorse_home()
    home.mkdir(parents=True, exist_ok=True)
    pid_file = home / "cron.pid"
    pid_file.write_text(str(os.getpid()))

    sched.start()
    log.info("cron up; %d jobs loaded", loaded)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows / restricted envs — fall back to KeyboardInterrupt.
            pass
    try:
        await stop.wait()
    finally:
        sched.shutdown(wait=False)
        if pid_file.exists():
            pid_file.unlink()


def run_scheduler_blocking() -> None:
    """CLI entrypoint: block the current thread until a shutdown signal."""
    asyncio.run(run_scheduler())
