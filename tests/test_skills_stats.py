"""Tests for the per-skill usage statistics sidecar (Phase 20)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from lite_horse.agent.instructions import _skills_index
from lite_horse.skills import stats as skill_stats
from lite_horse.skills.manage_tool import dispatch as skill_dispatch
from lite_horse.skills.view_tool import _view


def _write_skill(root: Path, name: str) -> None:
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: x\n---\n", encoding="utf-8"
    )


def test_sidecar_created_on_first_record(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "alpha")
    assert skill_stats.read("alpha") is None  # no sidecar yet

    skill_stats.record_view("alpha")

    sidecar = litehorse_home / "skills" / "alpha" / ".stats.json"
    assert sidecar.is_file()
    data = json.loads(sidecar.read_text("utf-8"))
    assert data["usage_count"] == 1
    assert data["success_count"] == 0
    assert data["error_count"] == 0
    assert data["last_used_at"] is not None
    assert data["schema_version"] == skill_stats.SCHEMA_VERSION


def test_record_view_increments_monotonically(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "beta")
    for _ in range(5):
        skill_stats.record_view("beta")
    data = skill_stats.read("beta")
    assert data is not None
    assert data["usage_count"] == 5


def test_record_outcome_success_path(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "gamma")
    skill_stats.record_view("gamma")
    skill_stats.record_outcome("gamma", ok=True)
    data = skill_stats.read("gamma")
    assert data is not None
    assert data["success_count"] == 1
    assert data["error_count"] == 0
    assert data["last_error_at"] is None
    assert data["last_error_summary"] is None


def test_record_outcome_error_path_captures_summary(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "delta")
    skill_stats.record_view("delta")
    skill_stats.record_outcome(
        "delta", ok=False, error_summary="tool returned success:false"
    )
    data = skill_stats.read("delta")
    assert data is not None
    assert data["success_count"] == 0
    assert data["error_count"] == 1
    assert data["last_error_at"] is not None
    assert data["last_error_summary"] == "tool returned success:false"


def test_record_outcome_error_summary_truncated(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "eps")
    huge = "x" * 5000
    skill_stats.record_outcome("eps", ok=False, error_summary=huge)
    data = skill_stats.read("eps")
    assert data is not None
    assert data["last_error_summary"] is not None
    assert len(data["last_error_summary"]) <= 500


def test_invalid_names_are_noops(litehorse_home: Path) -> None:
    del litehorse_home
    # Bad slug must not touch disk.
    skill_stats.record_view("../escape")
    skill_stats.record_view("Bad Slug")
    skill_stats.record_outcome("../escape", ok=True)
    # read returns None for invalid / missing
    assert skill_stats.read("Bad Slug") is None
    assert skill_stats.read("../escape") is None


def test_missing_skill_returns_none(litehorse_home: Path) -> None:
    del litehorse_home
    assert skill_stats.read("never-existed") is None


def test_concurrent_writes_do_not_clobber(litehorse_home: Path) -> None:
    """Hammer ``record_view`` from many threads; every tick must land."""
    _write_skill(litehorse_home, "zeta")

    n_threads = 8
    per_thread = 25

    def worker() -> None:
        for _ in range(per_thread):
            skill_stats.record_view("zeta")

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = skill_stats.read("zeta")
    assert data is not None
    assert data["usage_count"] == n_threads * per_thread


def test_mark_optimized_stamps_timestamp(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "eta")
    skill_stats.record_view("eta")
    assert skill_stats.read("eta")["last_optimized_at"] is None  # type: ignore[index]
    skill_stats.mark_optimized("eta")
    data = skill_stats.read("eta")
    assert data is not None
    assert data["last_optimized_at"] is not None


def test_hundred_sequential_views_hit_acceptance(litehorse_home: Path) -> None:
    """Phase 20 acceptance: 100 sequential runs yield usage_count == 100."""
    _write_skill(litehorse_home, "acceptance")
    for _ in range(100):
        skill_stats.record_view("acceptance")
    data = skill_stats.read("acceptance")
    assert data is not None
    assert data["usage_count"] == 100
    assert data["error_count"] == 0
    assert data["success_count"] == 0


def test_view_tool_integration_records_usage(litehorse_home: Path) -> None:
    """End-to-end: calling the view dispatch helper stamps the sidecar."""
    del litehorse_home
    skill_dispatch(
        "create",
        name="wired",
        content="---\nname: wired\ndescription: plumbing test\n---\n\n# body\n",
    )
    result = _view("wired")
    assert result["success"] is True

    data = skill_stats.read("wired")
    assert data is not None
    assert data["usage_count"] == 1

    # Missing / rejected views must NOT record usage.
    _view("ghost")
    _view("../escape")
    assert skill_stats.read("ghost") is None
    assert skill_stats.read("../escape") is None


def test_corrupted_sidecar_is_reset_on_next_write(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "corrupt")
    sidecar = litehorse_home / "skills" / "corrupt" / ".stats.json"
    sidecar.write_text("this is not json", encoding="utf-8")
    skill_stats.record_view("corrupt")
    data = skill_stats.read("corrupt")
    assert data is not None
    assert data["usage_count"] == 1


def test_fragile_decay_tag_triggers_in_index(litehorse_home: Path) -> None:
    """3+ errors and <50% success rate → fragile marker in the skills index."""
    _write_skill(litehorse_home, "shaky")
    for _ in range(3):
        skill_stats.record_view("shaky")
        skill_stats.record_outcome("shaky", ok=False, error_summary="boom")

    index = _skills_index()
    assert "**shaky**" in index
    assert "fragile" in index


def test_skill_with_good_ratio_is_not_marked_fragile(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "solid")
    for _ in range(10):
        skill_stats.record_view("solid")
        skill_stats.record_outcome("solid", ok=True)
    # One error out of 10 is still fine.
    skill_stats.record_outcome("solid", ok=False, error_summary="nope")

    index = _skills_index()
    assert "**solid**" in index
    assert "fragile" not in index


def test_skill_with_few_errors_is_not_marked_fragile(litehorse_home: Path) -> None:
    """Two errors shouldn't trip the fragile threshold — need at least 3."""
    _write_skill(litehorse_home, "fresh")
    for _ in range(2):
        skill_stats.record_view("fresh")
        skill_stats.record_outcome("fresh", ok=False, error_summary="x")

    index = _skills_index()
    assert "fragile" not in index
