"""Tests for the APScheduler cron wiring (Phase 10)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from apscheduler.triggers.cron import CronTrigger

from lite_horse.config import Config
from lite_horse.cron import scheduler as sched_mod
from lite_horse.cron.jobs import Job, JobStore
from lite_horse.cron.scheduler import (
    make_fire,
    parse_schedule,
    run_scheduler,
)
from lite_horse.sessions.db import SessionDB


class _StubResult:
    def __init__(self, text: str) -> None:
        self.final_output = text


def _cfg() -> Config:
    return Config.model_validate({"model": "gpt-test"})


# ---------- parse_schedule ----------


def test_parse_schedule_accepts_aliases() -> None:
    for alias in ("@minutely", "@hourly", "@daily", "@weekly"):
        assert isinstance(parse_schedule(alias), CronTrigger)


def test_parse_schedule_accepts_crontab() -> None:
    assert isinstance(parse_schedule("*/5 * * * *"), CronTrigger)


def test_parse_schedule_rejects_unknown_alias() -> None:
    with pytest.raises(ValueError, match="unknown schedule alias"):
        parse_schedule("@never")


def test_parse_schedule_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_schedule("not a cron")


# ---------- make_fire ----------


@pytest.mark.asyncio
async def test_fire_runs_agent_and_delivers(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    captured_prompts: list[str] = []
    delivered: list[tuple[dict[str, Any], str]] = []

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
        captured_prompts.append(text)
        return _StubResult(f"answer:{text}")

    async def fake_deliver(spec: dict[str, Any], text: str) -> None:
        delivered.append((spec, text))

    monkeypatch.setattr(sched_mod.Runner, "run", fake_run)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    db = SessionDB()
    fire = make_fire(db=db, cfg=_cfg())
    job = Job(
        id="smoke",
        schedule="@hourly",
        prompt="ping",
        delivery={"platform": "log"},
    )

    await fire(job)

    assert captured_prompts == ["ping"]
    assert delivered == [({"platform": "log"}, "answer:ping")]


@pytest.mark.asyncio
async def test_fire_delivers_error_when_runner_raises(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    delivered: list[str] = []

    async def boom(agent: Any, text: str, **_kw: Any) -> _StubResult:
        raise RuntimeError("kaboom")

    async def fake_deliver(spec: dict[str, Any], text: str) -> None:
        delivered.append(text)

    monkeypatch.setattr(sched_mod.Runner, "run", boom)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    db = SessionDB()
    fire = make_fire(db=db, cfg=_cfg())
    job = Job(
        id="bad",
        schedule="@hourly",
        prompt="p",
        delivery={"platform": "log"},
    )

    await fire(job)

    assert len(delivered) == 1
    assert delivered[0].startswith("⚠ cron job bad failed:")
    assert "kaboom" in delivered[0]


@pytest.mark.asyncio
async def test_fire_ends_session_after_run(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
        return _StubResult("ok")

    async def fake_deliver(spec: dict[str, Any], text: str) -> None:
        pass

    monkeypatch.setattr(sched_mod.Runner, "run", fake_run)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    db = SessionDB()
    ended: list[tuple[str, str]] = []
    original_end = db.end_session

    def spy(session_id: str, *, end_reason: str = "user_exit") -> None:
        ended.append((session_id, end_reason))
        original_end(session_id, end_reason=end_reason)

    monkeypatch.setattr(db, "end_session", spy)

    fire = make_fire(db=db, cfg=_cfg())
    job = Job(id="j", schedule="@hourly", prompt="p", delivery={"platform": "log"})
    await fire(job)

    assert len(ended) == 1
    assert ended[0][0].startswith("cron-j-")
    assert ended[0][1] == "cron_done"


# ---------- deliver ----------


@pytest.mark.asyncio
async def test_deliver_log_writes_to_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="lite_horse.cron.scheduler")
    await sched_mod.deliver({"platform": "log"}, "hello world")
    assert any("hello world" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_deliver_unknown_platform_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="lite_horse.cron.scheduler")
    await sched_mod.deliver({"platform": "carrier-pigeon"}, "msg")
    assert any("unknown delivery platform" in r.message for r in caplog.records)


# ---------- _schedule_jobs + run_scheduler ----------


def test_schedule_jobs_skips_disabled_and_invalid(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    store = JobStore()
    store.add(schedule="@hourly", prompt="a", delivery={"platform": "log"})
    store.add(
        schedule="@hourly", prompt="b", delivery={"platform": "log"}, enabled=False
    )
    store.add(schedule="not-a-cron", prompt="c", delivery={"platform": "log"})

    added: list[dict[str, Any]] = []

    class _StubScheduler:
        def add_job(self, fire: Any, trig: Any, **kwargs: Any) -> None:
            added.append({"trig": trig, **kwargs})

    async def dummy_fire(job: Job) -> None:
        pass

    loaded = sched_mod._schedule_jobs(_StubScheduler(), store, dummy_fire)  # type: ignore[arg-type]
    assert loaded == 1
    assert len(added) == 1
    assert added[0]["replace_existing"] is True


@pytest.mark.asyncio
async def test_run_scheduler_writes_pid_and_cleans_up(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No jobs — scheduler starts, stops immediately.
    pid_observed: list[bool] = []
    started: list[bool] = []
    shutdowns: list[bool] = []

    class _StubScheduler:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

        def add_job(self, *a: Any, **kw: Any) -> None:  # pragma: no cover
            pass

        def start(self) -> None:
            started.append(True)
            pid_observed.append((litehorse_home / "cron.pid").exists())

        def shutdown(self, *, wait: bool = True) -> None:
            shutdowns.append(True)

    monkeypatch.setattr(sched_mod, "AsyncIOScheduler", _StubScheduler)

    class _ImmediateEvent(asyncio.Event):
        async def wait(self) -> bool:
            return True

    monkeypatch.setattr(sched_mod.asyncio, "Event", _ImmediateEvent)

    await run_scheduler()

    assert started == [True]
    assert shutdowns == [True]
    assert pid_observed == [True]
    assert not (litehorse_home / "cron.pid").exists()
