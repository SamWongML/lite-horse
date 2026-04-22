"""Tests for conditional skill activation (Phase 21)."""
from __future__ import annotations

from pathlib import Path

from lite_horse.skills.activation import (
    SkillEntry,
    _parse_frontmatter,
    filter_for_turn,
)


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "no description",
    activate_when: str | None = None,
    category: str | None = None,
) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    if category:
        lines.append(f"category: {category}")
    if activate_when is not None:
        lines.append(activate_when.rstrip())
    lines.append("---")
    lines.append("")
    lines.append(f"# {name}\n")
    (d / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _skills_dir(litehorse_home: Path) -> Path:
    d = litehorse_home / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- load + parse ----------


def test_empty_skills_dir_returns_empty(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    assert filter_for_turn(skills_dir=d, user_text="anything") == []


def test_missing_skills_dir_returns_empty(litehorse_home: Path) -> None:
    missing = litehorse_home / "nope"
    assert filter_for_turn(skills_dir=missing, user_text="anything") == []


def test_parse_frontmatter_malformed_block_is_tolerated(
    litehorse_home: Path,
) -> None:
    d = _skills_dir(litehorse_home)
    (d / "brokenskill").mkdir()
    (d / "brokenskill" / "SKILL.md").write_text(
        "---\nname: brokenskill\ndescription: broken\n: not valid yaml:: [\n---\n",
        encoding="utf-8",
    )
    entries = filter_for_turn(skills_dir=d, user_text=None)
    # Still listed (fallback path), just treated as always-on with empty desc.
    assert [e.name for e in entries] == ["brokenskill"]


def test_parse_frontmatter_no_frontmatter_returns_empty_fm() -> None:
    assert _parse_frontmatter("# just a body\n") == {}


# ---------- fallback ----------


def test_user_text_none_returns_all_alphabetical(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(d, "charlie")
    _write_skill(d, "alpha")
    _write_skill(d, "bravo")
    names = [e.name for e in filter_for_turn(skills_dir=d, user_text=None)]
    assert names == ["alpha", "bravo", "charlie"]


# ---------- keyword match ----------


def test_keyword_match_beats_always_on(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "deploy-helper",
        description="ship a release",
        activate_when='activate_when:\n  - keywords: ["deploy", "ship"]',
    )
    _write_skill(d, "plan", description="planning")  # always-on
    entries = filter_for_turn(skills_dir=d, user_text="please deploy the app")
    names = [e.name for e in entries]
    assert "deploy-helper" in names
    assert "plan" in names  # always-on still surfaces (0.5 score)


def test_keyword_miss_excludes_specialist(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "devops",
        description="ship a release",
        activate_when='activate_when:\n  - keywords: ["deploy", "ship"]',
    )
    _write_skill(d, "plan", description="planning")
    names = [e.name for e in filter_for_turn(skills_dir=d, user_text="write a poem")]
    assert "devops" not in names
    assert "plan" in names


def test_keyword_case_insensitive(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "devops",
        description="release",
        activate_when='activate_when:\n  - keywords: ["Deploy"]',
    )
    names = [e.name for e in filter_for_turn(skills_dir=d, user_text="DEPLOY now")]
    assert names == ["devops"]


# ---------- file glob ----------


def test_file_glob_match(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "docker",
        description="containers",
        activate_when=(
            'activate_when:\n  - file_globs: ["Dockerfile", "k8s/*.yaml"]'
        ),
    )
    names = [
        e.name
        for e in filter_for_turn(
            skills_dir=d, user_text="open Dockerfile and tweak the CMD"
        )
    ]
    assert names == ["docker"]


def test_file_glob_wildcard(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "k8s",
        description="kubernetes",
        activate_when='activate_when:\n  - file_globs: ["*.yaml"]',
    )
    names = [
        e.name
        for e in filter_for_turn(
            skills_dir=d, user_text="update deploy.yaml please"
        )
    ]
    assert names == ["k8s"]


def test_file_glob_no_match(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "docker",
        description="containers",
        activate_when='activate_when:\n  - file_globs: ["Dockerfile"]',
    )
    names = [
        e.name
        for e in filter_for_turn(skills_dir=d, user_text="rename a python file")
    ]
    assert names == []


# ---------- USER.md signal ----------


def test_user_profile_keyword_gives_partial_score(litehorse_home: Path) -> None:
    memories = litehorse_home / "memories"
    memories.mkdir()
    (memories / "USER.md").write_text(
        "User works on kubernetes clusters daily.", encoding="utf-8"
    )
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "k8s",
        description="kubernetes",
        activate_when='activate_when:\n  - keywords: ["kubernetes"]',
    )
    # User text doesn't mention kubernetes, but USER.md does → score 1.0 > 0.
    names = [
        e.name
        for e in filter_for_turn(
            skills_dir=d, user_text="help me with something"
        )
    ]
    assert names == ["k8s"]


# ---------- top-K cap ----------


def test_top_k_cap_applied(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    for i in range(25):
        _write_skill(
            d,
            f"skill{i:02d}",
            description=f"skill {i}",
            activate_when='activate_when:\n  - keywords: ["trigger"]',
        )
    entries = filter_for_turn(
        skills_dir=d, user_text="trigger this please", top_k=8
    )
    assert len(entries) == 8


def test_top_k_cap_with_mixed_library(litehorse_home: Path) -> None:
    # 25 activatable (score 2.0) + 3 always-on (score 0.5). Top-K=8 should
    # prefer specialists.
    d = _skills_dir(litehorse_home)
    for i in range(25):
        _write_skill(
            d,
            f"match{i:02d}",
            description="matches",
            activate_when='activate_when:\n  - keywords: ["deploy"]',
        )
    _write_skill(d, "always-a")
    _write_skill(d, "always-b")
    _write_skill(d, "always-c")
    entries = filter_for_turn(skills_dir=d, user_text="please deploy it", top_k=8)
    assert len(entries) == 8
    assert all(e.name.startswith("match") for e in entries)


# ---------- scoring shape ----------


def test_always_on_without_match_still_surfaces(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(d, "plain")  # always-on, score 0.5
    _write_skill(
        d,
        "specialist",
        description="s",
        activate_when='activate_when:\n  - keywords: ["xyzzy"]',
    )
    names = [e.name for e in filter_for_turn(skills_dir=d, user_text="hello world")]
    assert names == ["plain"]


def test_skill_entry_always_on_flag(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(d, "plain")
    _write_skill(
        d,
        "specialist",
        description="s",
        activate_when='activate_when:\n  - keywords: ["foo"]',
    )
    entries = filter_for_turn(skills_dir=d, user_text=None)
    by_name = {e.name: e for e in entries}
    assert isinstance(by_name["plain"], SkillEntry)
    assert by_name["plain"].always_on is True
    assert by_name["specialist"].always_on is False


def test_category_parsed(litehorse_home: Path) -> None:
    d = _skills_dir(litehorse_home)
    _write_skill(
        d,
        "devops",
        description="s",
        category="devops",
        activate_when='activate_when:\n  - keywords: ["deploy"]',
    )
    entries = filter_for_turn(skills_dir=d, user_text="deploy it")
    assert entries[0].category == "devops"
