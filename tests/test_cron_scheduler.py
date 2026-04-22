"""Tests for the APScheduler cron wiring (Phase 10)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import openai
import pytest
from apscheduler.triggers.cron import CronTrigger
from httpx import Request, Response

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

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        delivered.append((spec, text))
        del session_key

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

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        delivered.append(text)
        del spec, session_key

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
    assert delivered[0].startswith("⚠ cron job bad failed (unknown):")
    assert "kaboom" in delivered[0]


@pytest.mark.asyncio
async def test_fire_ends_session_after_run(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home

    async def fake_run(agent: Any, text: str, **_kw: Any) -> _StubResult:
        return _StubResult("ok")

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        del spec, text, session_key

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


# ---------- Phase 22: error classifier + strike-based disable ----------


def _rate_limit_exc() -> openai.RateLimitError:
    resp = Response(429, request=Request("POST", "https://api.openai.com/v1/x"))
    return openai.RateLimitError("slow down", response=resp, body=None)


@pytest.mark.asyncio
async def test_fire_classifies_rate_limit_and_does_not_strike(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    delivered: list[str] = []

    async def blow_up(agent: Any, text: str, **_kw: Any) -> Any:
        del agent, text
        raise _rate_limit_exc()

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        delivered.append(text)
        del spec, session_key

    monkeypatch.setattr(sched_mod.Runner, "run", blow_up)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    store = JobStore()
    job = store.add(schedule="@hourly", prompt="p", delivery={"platform": "log"})
    fire = make_fire(db=SessionDB(), cfg=_cfg(), store=store)

    for _ in range(5):
        await fire(job)

    # Non-refusal errors never auto-disable.
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.enabled is True
    assert loaded.disabled_reason is None
    assert all("rate_limit" in msg for msg in delivered)


@pytest.mark.asyncio
async def test_fire_disables_job_after_three_model_refusals(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    delivered: list[str] = []

    async def refuse(agent: Any, text: str, **_kw: Any) -> Any:
        del agent, text
        raise openai.ContentFilterFinishReasonError()

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        delivered.append(text)
        del spec, session_key

    monkeypatch.setattr(sched_mod.Runner, "run", refuse)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    store = JobStore()
    job = store.add(schedule="@hourly", prompt="p", delivery={"platform": "log"})
    fire = make_fire(db=SessionDB(), cfg=_cfg(), store=store)

    # First two firings: still enabled, strike counter building up.
    await fire(job)
    assert store.get(job.id) is not None
    assert store.get(job.id).enabled is True  # type: ignore[union-attr]

    await fire(job)
    assert store.get(job.id).enabled is True  # type: ignore[union-attr]

    # Third firing crosses the limit.
    await fire(job)
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.enabled is False
    assert loaded.disabled_reason is not None
    assert "model refusals" in loaded.disabled_reason
    assert len(delivered) == 3
    assert all("model_refusal" in msg for msg in delivered)


@pytest.mark.asyncio
async def test_fire_strike_counter_resets_on_success(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    sequence: list[Any] = [
        openai.ContentFilterFinishReasonError(),
        openai.ContentFilterFinishReasonError(),
        _StubResult("all good"),
        openai.ContentFilterFinishReasonError(),
        openai.ContentFilterFinishReasonError(),
    ]

    async def driven(agent: Any, text: str, **_kw: Any) -> Any:
        del agent, text
        nxt = sequence.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    async def fake_deliver(
        spec: dict[str, Any], text: str, session_key: str
    ) -> None:
        del spec, text, session_key

    monkeypatch.setattr(sched_mod.Runner, "run", driven)
    monkeypatch.setattr(sched_mod, "deliver", fake_deliver)

    store = JobStore()
    job = store.add(schedule="@hourly", prompt="p", delivery={"platform": "log"})
    fire = make_fire(db=SessionDB(), cfg=_cfg(), store=store)

    for _ in range(5):
        await fire(job)

    loaded = store.get(job.id)
    assert loaded is not None
    # Only 2 refusals accumulated after the success reset — below the limit.
    assert loaded.enabled is True
    assert loaded.disabled_reason is None


def test_set_enabled_clears_disabled_reason(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    job = store.add(schedule="@hourly", prompt="p", delivery={"platform": "log"})
    store.disable_with_reason(job.id, "auto-disabled: 3 model refusals")

    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.enabled is False
    assert loaded.disabled_reason is not None

    assert store.set_enabled(job.id, True) is True
    loaded = store.get(job.id)
    assert loaded is not None
    assert loaded.enabled is True
    assert loaded.disabled_reason is None


# ---------- deliver ----------


@pytest.mark.asyncio
async def test_deliver_log_writes_to_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="lite_horse.cron.scheduler")
    await sched_mod.deliver({"platform": "log"}, "hello world", "sid-1")
    assert any("hello world" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_deliver_unknown_platform_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.ERROR, logger="lite_horse.cron.scheduler")
    await sched_mod.deliver({"platform": "carrier-pigeon"}, "msg", "sid-2")
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
