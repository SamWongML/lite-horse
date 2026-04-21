"""Tests for cron_manage dispatch (Phase 17)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.cron.jobs import JobStore
from lite_horse.cron.manage_tool import dispatch


def test_add_then_list_roundtrip(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@hourly",
        prompt="summarize open PRs",
        delivery_platform="webhook",
        delivery_url="https://webapp.test/cron",
        store=store,
    )
    assert result["success"] is True
    job = result["job"]
    assert job["schedule"] == "@hourly"
    assert job["prompt"] == "summarize open PRs"
    assert job["delivery"] == {
        "platform": "webhook",
        "url": "https://webapp.test/cron",
    }
    assert job["enabled"] is True

    listed = dispatch("list", store=store)
    assert listed["success"] is True
    assert len(listed["jobs"]) == 1
    assert listed["jobs"][0]["id"] == job["id"]


def test_add_log_platform_does_not_require_url(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@daily",
        prompt="p",
        delivery_platform="log",
        store=store,
    )
    assert result["success"] is True
    assert result["job"]["delivery"] == {"platform": "log"}


def test_add_rejects_unknown_schedule_alias(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@never",
        prompt="p",
        delivery_platform="log",
        store=store,
    )
    assert result["success"] is False
    assert "schedule" in result["error"].lower()
    assert store.all() == []


def test_add_rejects_malformed_crontab(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="not a cron",
        prompt="p",
        delivery_platform="log",
        store=store,
    )
    assert result["success"] is False
    assert store.all() == []


def test_add_requires_webhook_url(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@hourly",
        prompt="p",
        delivery_platform="webhook",
        store=store,
    )
    assert result["success"] is False
    assert "delivery_url" in result["error"]
    assert store.all() == []


@pytest.mark.parametrize(
    "bad_url",
    ["ftp://example.test/hook", "javascript:alert(1)", "file:///etc/passwd"],
)
def test_add_rejects_non_http_schemes(litehorse_home: Path, bad_url: str) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@hourly",
        prompt="p",
        delivery_platform="webhook",
        delivery_url=bad_url,
        store=store,
    )
    assert result["success"] is False
    assert "http" in result["error"].lower()
    assert store.all() == []


def test_add_rejects_unknown_platform(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch(
        "add",
        schedule="@hourly",
        prompt="p",
        delivery_platform="carrier-pigeon",
        store=store,
    )
    assert result["success"] is False
    assert "platform" in result["error"].lower()


def test_add_requires_schedule_and_prompt(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    assert dispatch("add", prompt="p", delivery_platform="log", store=store)[
        "success"
    ] is False
    assert dispatch(
        "add", schedule="@daily", delivery_platform="log", store=store
    )["success"] is False
    assert store.all() == []


def test_remove_happy_path(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    add = dispatch(
        "add",
        schedule="@daily",
        prompt="p",
        delivery_platform="log",
        store=store,
    )
    job_id = add["job"]["id"]
    assert dispatch("remove", job_id=job_id, store=store) == {"success": True}
    assert store.all() == []


def test_remove_missing_id_returns_error(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch("remove", job_id="nonexistent", store=store)
    assert result["success"] is False
    assert "nonexistent" in result["error"]


def test_remove_requires_job_id(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch("remove", store=store)
    assert result["success"] is False
    assert "job_id" in result["error"]


def test_enable_disable_flips_flag(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    add = dispatch(
        "add",
        schedule="@daily",
        prompt="p",
        delivery_platform="log",
        store=store,
    )
    job_id = add["job"]["id"]

    assert dispatch("disable", job_id=job_id, store=store) == {"success": True}
    assert store.get(job_id) is not None
    assert store.get(job_id).enabled is False  # type: ignore[union-attr]

    assert dispatch("enable", job_id=job_id, store=store) == {"success": True}
    assert store.get(job_id).enabled is True  # type: ignore[union-attr]


def test_enable_disable_missing_id(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    assert dispatch("enable", job_id="missing", store=store)["success"] is False
    assert dispatch("disable", job_id="missing", store=store)["success"] is False


def test_unknown_action(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    result = dispatch("nope", store=store)  # type: ignore[arg-type]
    assert result["success"] is False
    assert "unknown action" in result["error"]


def test_dispatch_defaults_to_shared_jobstore(litehorse_home: Path) -> None:
    """When no explicit store is passed the tool writes to the default path."""
    del litehorse_home
    result = dispatch(
        "add",
        schedule="@daily",
        prompt="p",
        delivery_platform="log",
    )
    assert result["success"] is True

    # A fresh JobStore on the default path sees the same job.
    fresh = JobStore()
    jobs = fresh.all()
    assert len(jobs) == 1
    assert jobs[0].id == result["job"]["id"]
