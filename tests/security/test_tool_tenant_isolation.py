"""Phase 40 acceptance: tool writes route through the per-call backend.

The pre-Phase-40 bug was that ``memory_tool`` / ``skill_manage`` /
``cron_manage`` reached into ``litehorse_home()`` regardless of the
caller's ``user_id``, so on a multi-task ECS deploy User A's writes
either landed in a file User B read, or vanished on container restart.

This module spins two in-memory cloud backends scoped to two distinct
``user_id`` values (no Postgres, no FastAPI), drives the tool bodies
through ``Runner.run_streamed``-shaped ``RunContextWrapper`` stand-ins,
and asserts that A's writes never reach B's read view. The full DB-backed
RLS leak gate lives in :mod:`tests.security.test_rls_leak`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from agents.tool_context import ToolContext

from lite_horse.agent.backends import (
    CronBackend,
    CronJobView,
    MemoryBackend,
    RecallBackend,
    Recalled,
    SkillBackend,
    SourceKind,
    TenantContext,
)
from lite_horse.cron.manage_tool import cron_manage
from lite_horse.effective import ResolvedSkill
from lite_horse.memory.tool import memory_tool
from lite_horse.skills.manage_tool import skill_manage

# ---------------- in-memory backends keyed by user_id ----------------


class _InMemoryMemoryBackend(MemoryBackend):
    def __init__(self) -> None:
        self.docs: dict[str, list[str]] = {"memory": [], "user": []}
        self.limits: dict[str, int] = {"memory": 2200, "user": 1375}

    async def get(self, kind: Any) -> str:
        return "\n§\n".join(self.docs[kind])

    async def entries(self, kind: Any) -> list[str]:
        return list(self.docs[kind])

    async def total_chars(self, kind: Any) -> int:
        es = self.docs[kind]
        return sum(len(e) for e in es) + max(0, len(es) - 1) * len("\n§\n")

    def char_limit(self, kind: Any) -> int:
        return self.limits[kind]

    async def add(self, kind: Any, content: str) -> None:
        self.docs[kind].append(content)

    async def replace(self, kind: Any, old: str, new: str) -> None:
        for i, e in enumerate(self.docs[kind]):
            if old in e:
                self.docs[kind][i] = new
                return
        raise ValueError(f"no entry matches: {old!r}")

    async def remove(self, kind: Any, old: str) -> None:
        for i, e in enumerate(self.docs[kind]):
            if old in e:
                self.docs[kind].pop(i)
                return
        raise ValueError(f"no entry matches: {old!r}")


class _InMemorySkillBackend(SkillBackend):
    def __init__(self) -> None:
        self.skills: dict[str, str] = {}

    async def list_slugs(self) -> list[str]:
        return sorted(self.skills)

    async def list_resolved(self) -> list[ResolvedSkill]:
        return []

    async def view(self, slug: str) -> dict[str, Any]:
        if slug not in self.skills:
            return {"success": False, "error": f"skill {slug!r} not found"}
        return {"success": True, "name": slug, "content": self.skills[slug]}

    async def read_md(self, slug: str) -> str | None:
        return self.skills.get(slug)

    async def create(self, *, slug: str, content: str) -> dict[str, Any]:
        self.skills[slug] = content
        return {"success": True, "path": f"skills/{slug}/SKILL.md"}

    async def patch(
        self, *, slug: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        if slug not in self.skills:
            return {"success": False, "error": "missing"}
        self.skills[slug] = self.skills[slug].replace(old_string, new_string)
        return {"success": True}

    async def edit(self, *, slug: str, content: str) -> dict[str, Any]:
        self.skills[slug] = content
        return {"success": True}

    async def delete(self, *, slug: str) -> dict[str, Any]:
        self.skills.pop(slug, None)
        return {"success": True}

    async def write_file(
        self, *, slug: str, file_path: str, content: str
    ) -> dict[str, Any]:
        return {"success": True, "path": f"skills/{slug}/{file_path}"}

    async def remove_file(
        self, *, slug: str, file_path: str
    ) -> dict[str, Any]:
        return {"success": True}

    async def record_view(self, slug: str) -> None:
        return None

    async def record_outcome(
        self, slug: str, *, ok: bool, error_summary: str | None = None
    ) -> None:
        return None

    async def fragile_suffix(self, slug: str) -> str:
        return ""


class _InMemoryCronBackend(CronBackend):
    def __init__(self) -> None:
        self.jobs: list[CronJobView] = []
        self._counter = 0

    async def list_jobs(self) -> list[CronJobView]:
        return list(self.jobs)

    async def add(
        self, *, schedule: str, prompt: str, delivery: dict[str, Any]
    ) -> CronJobView:
        self._counter += 1
        view = CronJobView(
            id=f"job-{self._counter}",
            schedule=schedule,
            prompt=prompt,
            delivery=dict(delivery),
            enabled=True,
        )
        self.jobs.append(view)
        return view

    async def remove(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.id != job_id]
        return len(self.jobs) < before

    async def set_enabled(self, job_id: str, *, enabled: bool) -> bool:
        for j in self.jobs:
            if j.id == job_id:
                j_dict = j.__dict__.copy()
                j_dict["enabled"] = enabled
                self.jobs[self.jobs.index(j)] = CronJobView(**j_dict)
                return True
        return False


# ---------------- ctx stand-in carrying the TenantContext ----------------


class _InMemoryRecallBackend(RecallBackend):
    def __init__(self) -> None:
        self.rows: list[tuple[str, str | None, str]] = []

    async def index(
        self, *, source_kind: SourceKind, source_id: str | None, content: str
    ) -> int:
        self.rows = [r for r in self.rows if r[:2] != (source_kind, source_id)]
        self.rows.append((source_kind, source_id, content))
        return 1

    async def search(self, query: str, *, k: int = 5) -> list[Recalled]:
        out: list[Recalled] = []
        ql = query.lower()
        for kind, src_id, content in self.rows:
            if ql in content.lower():
                out.append(
                    Recalled(
                        source_kind=kind,
                        source_id=src_id,
                        content=content,
                        score=1.0,
                        ts_iso="",
                    )
                )
        return out[:k]

    async def delete(
        self, *, source_kind: SourceKind, source_id: str | None
    ) -> int:
        before = len(self.rows)
        self.rows = [r for r in self.rows if r[:2] != (source_kind, source_id)]
        return before - len(self.rows)


def _make_ctx(
    user_id: str, *, tool_name: str = "tool"
) -> ToolContext[TenantContext]:
    from lite_horse.agent.backends.feedback_local import FeedbackLocalBackend

    tenant = TenantContext(
        user_id=user_id,
        agent_id=None,
        memory=_InMemoryMemoryBackend(),
        skill=_InMemorySkillBackend(),
        cron=_InMemoryCronBackend(),
        recall=_InMemoryRecallBackend(),
        feedback=FeedbackLocalBackend(),
    )
    return ToolContext(
        context=tenant,
        tool_name=tool_name,
        tool_call_id="tc-1",
        tool_arguments="{}",
    )


# ---------------- tests ----------------


@pytest.mark.asyncio
async def test_memory_writes_isolate_per_tenant() -> None:
    a = _make_ctx("user-a", tool_name="memory")
    b = _make_ctx("user-b", tool_name="memory")

    await memory_tool.on_invoke_tool(  # type: ignore[attr-defined]
        a,
        json.dumps(
            {"action": "add", "target": "memory", "content": "A's secret"}
        ),
    )

    a_entries = await a.context.memory.entries("memory")
    b_entries = await b.context.memory.entries("memory")
    assert "A's secret" in a_entries
    assert "A's secret" not in b_entries
    assert b_entries == []


@pytest.mark.asyncio
async def test_skill_writes_isolate_per_tenant() -> None:
    a = _make_ctx("user-a", tool_name="memory")
    b = _make_ctx("user-b", tool_name="memory")

    skill_md = (
        "---\nname: a-only\ndescription: only A's\n---\n\n# body\n"
    )
    await skill_manage.on_invoke_tool(  # type: ignore[attr-defined]
        a,
        json.dumps(
            {"action": "create", "name": "a-only", "content": skill_md}
        ),
    )

    a_list = await a.context.skill.list_slugs()
    b_list = await b.context.skill.list_slugs()
    assert "a-only" in a_list
    assert "a-only" not in b_list
    assert b_list == []


@pytest.mark.asyncio
async def test_cron_writes_isolate_per_tenant() -> None:
    a = _make_ctx("user-a", tool_name="memory")
    b = _make_ctx("user-b", tool_name="memory")

    await cron_manage.on_invoke_tool(  # type: ignore[attr-defined]
        a,
        json.dumps(
            {
                "action": "add",
                "schedule": "@hourly",
                "prompt": "summarize PRs",
                "delivery_platform": "log",
            }
        ),
    )

    a_jobs = await a.context.cron.list_jobs()
    b_jobs = await b.context.cron.list_jobs()
    assert any(j.prompt == "summarize PRs" for j in a_jobs)
    assert b_jobs == []
