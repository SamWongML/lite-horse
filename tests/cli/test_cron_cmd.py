"""Tests for `litehorse cron {list, add, enable, disable, remove, run-once}`.

The `scheduler` subcommand is exercised via subprocess in
`tests/cli/test_cron_scheduler_subprocess.py` (SIGTERM shutdown). Here we
stay in-process.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.cron import app
from lite_horse.cli.exit_codes import ExitCode
from lite_horse.cron.jobs import JobStore


def test_list_empty(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "0 jobs" in result.stdout


def test_add_happy_path(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [
        "add", "@daily", "write a haiku", "--platform", "log",
    ])
    assert result.exit_code == 0
    assert "added:" in result.stdout
    assert len(JobStore().all()) == 1


def test_add_rejects_bad_schedule(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["add", "not-a-crontab", "prompt"])
    assert result.exit_code == int(ExitCode.USAGE)


def test_add_webhook_requires_url(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [
        "add", "@daily", "prompt", "--platform", "webhook",
    ])
    assert result.exit_code == int(ExitCode.USAGE)


def test_add_webhook_rejects_non_http(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, [
        "add", "@daily", "prompt",
        "--platform", "webhook",
        "--url", "ftp://evil.example",
    ])
    assert result.exit_code == int(ExitCode.USAGE)


def test_list_shows_added_jobs_json(litehorse_home: Path) -> None:
    job = JobStore().add(
        schedule="@hourly", prompt="p", delivery={"platform": "log"}
    )
    runner = CliRunner()
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    items = [
        json.loads(line) for line in result.stdout.splitlines()
    ]
    item_data = [r["data"] for r in items if r["kind"] == "item"]
    assert any(j["id"] == job.id for j in item_data)


def test_enable_disable_round_trip(litehorse_home: Path) -> None:
    job = JobStore().add(
        schedule="@daily", prompt="p", delivery={"platform": "log"}
    )
    runner = CliRunner()
    r1 = runner.invoke(app, ["disable", job.id])
    assert r1.exit_code == 0
    assert JobStore().get(job.id).enabled is False  # type: ignore[union-attr]
    r2 = runner.invoke(app, ["enable", job.id])
    assert r2.exit_code == 0
    assert JobStore().get(job.id).enabled is True  # type: ignore[union-attr]


def test_enable_missing_returns_not_found(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["enable", "nope"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


def test_remove_deletes_job(litehorse_home: Path) -> None:
    job = JobStore().add(
        schedule="@daily", prompt="p", delivery={"platform": "log"}
    )
    runner = CliRunner()
    result = runner.invoke(app, ["remove", job.id])
    assert result.exit_code == 0
    assert JobStore().get(job.id) is None


def test_run_once_missing_returns_not_found(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["run-once", "nope"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


def test_run_once_fires_closure(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stub ``make_fire`` so the subcommand runs without a real agent."""
    fired: list[str] = []

    async def _fake_fire(job: Any) -> None:
        fired.append(job.id)

    def _fake_make_fire(**_kwargs: Any) -> Any:
        return _fake_fire

    monkeypatch.setattr(
        "lite_horse.cli.commands.cron.make_fire",  # patch target: the import lives inside run_once
        _fake_make_fire,
        raising=False,
    )
    # The import inside `run_once` is `from lite_horse.cron.scheduler import make_fire`.
    # Patch the scheduler module path to catch it regardless of import style.
    monkeypatch.setattr(
        "lite_horse.cron.scheduler.make_fire", _fake_make_fire, raising=True
    )

    job = JobStore().add(
        schedule="@daily", prompt="p", delivery={"platform": "log"}
    )
    runner = CliRunner()
    result = runner.invoke(app, ["run-once", job.id])
    assert result.exit_code == 0, result.stdout
    assert fired == [job.id]
