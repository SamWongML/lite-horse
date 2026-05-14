"""Local writes land at the v0.4 paths byte-for-byte.

The contract from the v0.5 plan is "the ``litehorse`` CLI keeps booting
against ``~/.litehorse/`` with zero migrations and the same byte-level
state". This test exercises the local backends end-to-end (no LLM, no
SDK Runner) and asserts the produced files match the v0.4 layout:

- ``memory.add(content)`` writes ``~/.litehorse/memories/MEMORY.md``
  with the same delimiter shape as v0.4.
- ``skill.create(slug, SKILL.md)`` writes
  ``~/.litehorse/skills/<slug>/SKILL.md`` byte-for-byte.
- ``cron.add(...)`` appends to ``~/.litehorse/jobs.json``.

The cloud-mode equivalent is asserted indirectly by
:mod:`tests.security.test_tool_tenant_isolation` (against in-memory
backends) and :mod:`tests.security.test_rls_leak` (against Postgres + RLS).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lite_horse.agent.backends import build_local_tenant_context
from lite_horse.constants import ENTRY_DELIMITER

_VALID_SKILL = (
    "---\n"
    "name: example\n"
    "description: A skill written through the local backend.\n"
    "---\n\n"
    "# example\n\nBody.\n"
)


@pytest.mark.asyncio
async def test_memory_add_writes_byte_for_byte(litehorse_home: Path) -> None:
    tenant = build_local_tenant_context()
    await tenant.memory.add("memory", "I prefer pnpm")

    md = litehorse_home / "memories" / "MEMORY.md"
    assert md.is_file()
    text = md.read_text(encoding="utf-8")
    # v0.4 shape: a single entry with a trailing newline.
    assert text == "I prefer pnpm\n"


@pytest.mark.asyncio
async def test_memory_two_adds_use_section_delimiter(
    litehorse_home: Path,
) -> None:
    tenant = build_local_tenant_context()
    await tenant.memory.add("memory", "first fact")
    await tenant.memory.add("memory", "second fact")

    md = litehorse_home / "memories" / "MEMORY.md"
    text = md.read_text(encoding="utf-8")
    expected = "first fact" + ENTRY_DELIMITER + "second fact" + "\n"
    assert text == expected


@pytest.mark.asyncio
async def test_skill_create_writes_to_skills_root(
    litehorse_home: Path,
) -> None:
    tenant = build_local_tenant_context()
    result = await tenant.skill.create(slug="example", content=_VALID_SKILL)
    assert result["success"] is True

    md = litehorse_home / "skills" / "example" / "SKILL.md"
    assert md.is_file()
    assert md.read_text(encoding="utf-8") == _VALID_SKILL


@pytest.mark.asyncio
async def test_cron_add_writes_to_jobs_json(litehorse_home: Path) -> None:
    tenant = build_local_tenant_context()
    job = await tenant.cron.add(
        schedule="@hourly",
        prompt="summarize PRs",
        delivery={"platform": "log"},
    )
    assert job.id

    jobs_file = litehorse_home / "jobs.json"
    raw = json.loads(jobs_file.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert len(raw) == 1
    assert raw[0]["schedule"] == "@hourly"
    assert raw[0]["prompt"] == "summarize PRs"
    assert raw[0]["delivery"] == {"platform": "log"}
    assert raw[0]["enabled"] is True


@pytest.mark.asyncio
async def test_user_md_writes_to_separate_file(litehorse_home: Path) -> None:
    """`memory.add('user', ...)` writes USER.md, not MEMORY.md."""
    tenant = build_local_tenant_context()
    await tenant.memory.add("user", "Name: Sam")

    user_md = litehorse_home / "memories" / "USER.md"
    memory_md = litehorse_home / "memories" / "MEMORY.md"
    assert user_md.is_file()
    assert user_md.read_text(encoding="utf-8") == "Name: Sam\n"
    # MEMORY.md should be empty (or absent — depends on whether we touched it).
    if memory_md.is_file():
        assert memory_md.read_text(encoding="utf-8") == ""
