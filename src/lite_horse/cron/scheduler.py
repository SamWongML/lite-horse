"""APScheduler wiring + delivery for scheduled cron jobs.

One ``AsyncIOScheduler`` runs on the main event loop; each firing builds a
fresh agent + session (source ``"cron"``) and hands the final output to a
delivery handler (log, Telegram). Shutdown is signal-driven — SIGINT / SIGTERM
stop the scheduler and remove ``cron.pid`` — mirroring the gateway.
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
from telegram import Bot

from lite_horse.agent.factory import build_agent
from lite_horse.config import Config, load_config
from lite_horse.constants import litehorse_home
from lite_horse.cron.jobs import Job, JobStore
from lite_horse.sessions.db import SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db

log = logging.getLogger(__name__)

Fire = Callable[[Job], Awaitable[None]]
Deliver = Callable[[dict[str, Any], str], Awaitable[None]]

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


async def deliver_log(_spec: dict[str, Any], text: str) -> None:
    log.info("[cron output] %s", text)


async def deliver_telegram(spec: dict[str, Any], text: str) -> None:
    """Send to Telegram via a fresh ``Bot`` (no shared state with the gateway)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("telegram delivery skipped: TELEGRAM_BOT_TOKEN not set")
        return
    chat_id = spec.get("chat_id")
    if chat_id is None:
        log.error("telegram delivery skipped: missing chat_id")
        return
    bot = Bot(token=token)
    await bot.send_message(chat_id=int(chat_id), text=text)


DELIVERY_HANDLERS: dict[str, Deliver] = {
    "log": deliver_log,
    "telegram": deliver_telegram,
}


async def deliver(spec: dict[str, Any], text: str) -> None:
    platform = spec.get("platform", "log")
    handler = DELIVERY_HANDLERS.get(platform)
    if handler is None:
        log.error("unknown delivery platform: %s", platform)
        return
    await handler(spec, text)


def make_fire(*, db: SessionDB, cfg: Config) -> Fire:
    """Build the closure APScheduler calls when a job is due.

    Exposed so tests can invoke a firing directly without standing up a real
    scheduler.
    """

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
            await deliver(job.delivery, str(result.final_output))
        except Exception as exc:
            log.exception("cron job %s failed", job.id)
            await deliver(job.delivery, f"⚠ cron job {job.id} failed: {exc}")
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
    db = SessionDB()
    bind_db(db)
    store = JobStore()
    sched = AsyncIOScheduler()

    fire = make_fire(db=db, cfg=cfg)
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
