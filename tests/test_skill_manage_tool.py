"""Tests for the skill_manage dispatch logic + bundled skill sync (Phase 3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from lite_horse.skills.manage_tool import dispatch
from lite_horse.skills.source import skills_root, sync_bundled_skills

_VALID_SKILL = """---
name: example
description: A skill used by tests; not loaded at runtime.
---

# example

Body.
"""


def test_create_then_list_shows_skill(litehorse_home: Path) -> None:
    del litehorse_home
    assert dispatch("create", name="example", content=_VALID_SKILL) == {
        "success": True,
        "path": "skills/example/SKILL.md",
    }
    listed = dispatch("list")
    assert listed["success"] is True
    assert "example" in listed["skills"]


@pytest.mark.parametrize(
    "bad_name",
    ["Example", "-leading-dash", "has space", "way-too-long-" + "x" * 100, ""],
)
def test_create_rejects_bad_names(litehorse_home: Path, bad_name: str) -> None:
    del litehorse_home
    result = dispatch("create", name=bad_name, content=_VALID_SKILL)
    assert result["success"] is False


def test_create_rejects_content_without_frontmatter(litehorse_home: Path) -> None:
    del litehorse_home
    result = dispatch("create", name="nofm", content="just a body")
    assert result["success"] is False
    assert "frontmatter" in result["error"].lower()


def test_create_rejects_duplicate(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="dup", content=_VALID_SKILL)
    result = dispatch("create", name="dup", content=_VALID_SKILL)
    assert result["success"] is False
    assert "already exists" in result["error"]


def test_patch_single_match(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    result = dispatch("patch", name="ex", old_string="# example", new_string="# renamed")
    assert result == {"success": True}
    text = (skills_root() / "ex" / "SKILL.md").read_text(encoding="utf-8")
    assert "# renamed" in text
    assert "# example" not in text


def test_patch_multi_match_returns_error(litehorse_home: Path) -> None:
    del litehorse_home
    body = _VALID_SKILL + "\n\nrepeat\n\nrepeat\n"
    dispatch("create", name="ex", content=body)
    result = dispatch("patch", name="ex", old_string="repeat", new_string="once")
    assert result["success"] is False
    assert "matches 2 times" in result["error"]


def test_patch_no_match_returns_error(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    result = dispatch("patch", name="ex", old_string="absent", new_string="x")
    assert result["success"] is False
    assert "not found" in result["error"]


def test_write_file_rejects_path_traversal(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    result = dispatch(
        "write_file",
        name="ex",
        file_path="../../etc/passwd",
        content="evil",
    )
    assert result["success"] is False
    assert "escapes" in result["error"]


def test_write_file_creates_supporting_file(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    result = dispatch(
        "write_file",
        name="ex",
        file_path="references/notes.md",
        content="hello",
    )
    assert result["success"] is True
    assert (skills_root() / "ex" / "references" / "notes.md").read_text() == "hello"


def test_remove_file_deletes_supporting_file(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    dispatch("write_file", name="ex", file_path="ref.md", content="x")
    result = dispatch("remove_file", name="ex", file_path="ref.md")
    assert result == {"success": True}
    assert not (skills_root() / "ex" / "ref.md").exists()


def test_delete_removes_directory(litehorse_home: Path) -> None:
    del litehorse_home
    dispatch("create", name="ex", content=_VALID_SKILL)
    assert dispatch("delete", name="ex") == {"success": True}
    assert not (skills_root() / "ex").exists()


def test_sync_bundled_skills_first_run_then_idempotent(litehorse_home: Path) -> None:
    first = sync_bundled_skills()
    assert "plan" in first
    assert "skill-creator" in first
    assert (litehorse_home / "skills" / "plan" / "SKILL.md").exists()
    second = sync_bundled_skills()
    assert second == []  # idempotent


def test_sync_bundled_skills_does_not_clobber_user_edits(litehorse_home: Path) -> None:
    sync_bundled_skills()
    user_edited = litehorse_home / "skills" / "plan" / "SKILL.md"
    user_edited.write_text("---\nname: plan\ndescription: my edit\n---\n", encoding="utf-8")
    sync_bundled_skills()
    assert "my edit" in user_edited.read_text(encoding="utf-8")
