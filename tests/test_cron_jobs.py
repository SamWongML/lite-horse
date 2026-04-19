"""Tests for the JSON-backed cron job store (Phase 10)."""
from __future__ import annotations

import json
from pathlib import Path

from lite_horse.cron.jobs import Job, JobStore


def test_empty_file_materialized_on_init(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    assert store.path.exists()
    assert store.all() == []


def test_add_returns_job_and_persists(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    job = store.add(
        schedule="@hourly",
        prompt="what time is it?",
        delivery={"platform": "log"},
    )
    assert isinstance(job, Job)
    assert job.enabled is True
    assert len(job.id) == 12

    # Fresh instance reads the same data back.
    again = JobStore(path=store.path)
    loaded = again.all()
    assert len(loaded) == 1
    assert loaded[0].id == job.id
    assert loaded[0].schedule == "@hourly"
    assert loaded[0].delivery == {"platform": "log"}


def test_remove_returns_false_for_missing_id(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    store.add(schedule="@daily", prompt="p", delivery={"platform": "log"})
    assert store.remove("does-not-exist") is False
    assert len(store.all()) == 1


def test_remove_deletes_matching_job(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    a = store.add(schedule="@daily", prompt="a", delivery={"platform": "log"})
    b = store.add(schedule="@hourly", prompt="b", delivery={"platform": "log"})

    assert store.remove(a.id) is True
    remaining = store.all()
    assert [j.id for j in remaining] == [b.id]


def test_get_returns_job_or_none(litehorse_home: Path) -> None:
    del litehorse_home
    store = JobStore()
    job = store.add(schedule="@daily", prompt="p", delivery={"platform": "log"})
    assert store.get(job.id) == job
    assert store.get("missing") is None


def test_write_is_atomic(litehorse_home: Path) -> None:
    """No stray ``.tmp`` should remain after a successful write."""
    del litehorse_home
    store = JobStore()
    store.add(schedule="@hourly", prompt="p", delivery={"platform": "log"})
    tmp = store.path.with_suffix(store.path.suffix + ".tmp")
    assert not tmp.exists()
    # And the file itself is valid JSON.
    assert isinstance(json.loads(store.path.read_text()), list)
