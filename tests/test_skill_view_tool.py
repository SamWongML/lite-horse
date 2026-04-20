"""Tests for the ``skill_view`` dispatch helper (Phase 14)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.skills.manage_tool import dispatch
from lite_horse.skills.source import skills_root
from lite_horse.skills.view_tool import _VIEW_MAX_BYTES, _view

_VALID_SKILL = """---
name: example
description: A skill used by tests; not loaded at runtime.
---

# example

Body.
"""


def test_view_happy_path(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="example", content=_VALID_SKILL)
    result = _view("example")
    assert result["success"] is True
    assert result["name"] == "example"
    assert "# example" in result["content"]
    assert "description:" in result["content"]


def test_view_missing_skill(litehorse_home: Path) -> None:
    del litehorse_home
    result = _view("ghost")
    assert result["success"] is False
    assert "not found" in result["error"]


@pytest.mark.parametrize(
    "bad_name",
    ["Example", "-leading", "has space", "x" * 100, "", "../etc"],
)
def test_view_rejects_bad_names(litehorse_home: Path, bad_name: str) -> None:
    del litehorse_home
    result = _view(bad_name)
    assert result["success"] is False


def test_view_rejects_path_traversal_via_symlink(litehorse_home: Path) -> None:
    # Even if a slug validator missed a payload, resolve() + is_relative_to
    # guards against escapes. Sanity-check by pointing a symlinked skill dir at
    # the tmp root's sibling.
    root = skills_root()
    outside = litehorse_home / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside)
    result = _view("escape")
    # The symlinked target resolves outside the skills root, so view must
    # either reject the escape OR succeed only when the resolved target still
    # lives inside root. On POSIX tmp layouts the symlink target's real path
    # is outside root — this must fail.
    assert result["success"] is False


def test_view_caps_oversize_content(litehorse_home: Path) -> None:
    del litehorse_home
    big_body = "a" * (_VIEW_MAX_BYTES + 1024)
    dispatch(
        "create",
        name="big",
        content=f"---\nname: big\ndescription: oversize\n---\n\n{big_body}",
    )
    result = _view("big")
    assert result["success"] is True
    assert len(result["content"].encode("utf-8")) <= _VIEW_MAX_BYTES
    assert "truncated" in result["content"]
