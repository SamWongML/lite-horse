"""Unit tests for Phase 29 slash handlers (cron / memory / skills).

Each handler delegates to a helper in ``lite_horse.cli.commands.*`` so we
only need to seed the on-disk state and dispatch the slash command through
the real registry. That doubles as the structural check the plan calls for:
if a slash handler re-implements logic, we'd see it here too.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lite_horse.cli.repl.slash import ParsedSlash, dispatch
from lite_horse.cli.repl.slash_handlers.session import build_default_registry
from lite_horse.cron.jobs import JobStore
from lite_horse.memory.store import MemoryStore
from lite_horse.skills.source import skills_root


@dataclass
class StubState:
    session_key: str = "agent:main:cli:repl:local"
    model: str = "m-test"
    permission_mode: str = "auto"
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    debug: bool = False
    verbose: str = "new"
    total_tokens: int = 0
    ctx_max: int = 200_000
    total_cost_usd: float | None = None
    pending_attachments: list[Any] = field(default_factory=list)
    current_turn_task: Any = None
    registry: Any = None
    messages: list[str] = field(default_factory=list)
    expand_last_tool: bool = False

    def print_line(self, msg: str) -> None:
        self.messages.append(msg)


async def _dispatch(name: str, args: list[str], state: StubState) -> None:
    reg = build_default_registry()
    state.registry = reg
    await dispatch(reg, ParsedSlash(name=name, args=args), state)


# ---------- cron ----------


async def test_slash_cron_list_reports_empty(litehorse_home: Path) -> None:
    state = StubState()
    await _dispatch("cron", ["list"], state)
    assert any("no jobs" in m for m in state.messages)


async def test_slash_cron_add_and_list(litehorse_home: Path) -> None:
    state = StubState()
    await _dispatch("cron", ["add", "@daily", "write", "a", "haiku"], state)
    assert len(JobStore().all()) == 1
    state.messages.clear()
    await _dispatch("cron", ["list"], state)
    assert any("@daily" in m for m in state.messages)


async def test_slash_cron_enable_disable_remove(litehorse_home: Path) -> None:
    job = JobStore().add(
        schedule="@daily", prompt="p", delivery={"platform": "log"}
    )
    state = StubState()
    await _dispatch("cron", ["disable", job.id], state)
    assert JobStore().get(job.id).enabled is False  # type: ignore[union-attr]
    await _dispatch("cron", ["enable", job.id], state)
    assert JobStore().get(job.id).enabled is True  # type: ignore[union-attr]
    await _dispatch("cron", ["remove", job.id], state)
    assert JobStore().get(job.id) is None


async def test_slash_cron_rejects_bad_schedule(litehorse_home: Path) -> None:
    state = StubState()
    await _dispatch("cron", ["add", "not-a-crontab", "prompt"], state)
    assert JobStore().all() == []


# ---------- memory ----------


async def test_slash_memory_show_empty(litehorse_home: Path) -> None:
    state = StubState()
    await _dispatch("memory", ["show"], state)
    assert any("(empty)" in m for m in state.messages)


async def test_slash_memory_show_prints_entries(litehorse_home: Path) -> None:
    MemoryStore.for_memory().add("uses pnpm")
    state = StubState()
    await _dispatch("memory", ["show"], state)
    assert any("uses pnpm" in m for m in state.messages)


async def test_slash_memory_clear_user_only(litehorse_home: Path) -> None:
    MemoryStore.for_memory().add("agent note")
    MemoryStore.for_user().add("user note")
    state = StubState()
    await _dispatch("memory", ["clear", "--user"], state)
    assert MemoryStore.for_memory().entries() == ["agent note"]
    assert MemoryStore.for_user().entries() == []


# ---------- skills ----------


def _write_skill(name: str) -> Path:
    root = skills_root()
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    md = d / "SKILL.md"
    md.write_text(
        f"---\nname: {name}\nversion: 1\ndescription: stub\n---\nbody\n",
        encoding="utf-8",
    )
    return md


async def test_slash_skills_lists_installed(litehorse_home: Path) -> None:
    _write_skill("plan")
    _write_skill("review")
    state = StubState()
    await _dispatch("skills", [], state)
    joined = "\n".join(state.messages)
    assert "plan" in joined and "review" in joined


async def test_slash_skill_hint_stages_attachment(litehorse_home: Path) -> None:
    _write_skill("plan")
    state = StubState()
    await _dispatch("skill", ["plan"], state)
    assert any(a.get("kind") == "text" for a in state.pending_attachments)
    assert any("plan" in a["content"] for a in state.pending_attachments)


async def test_slash_skill_hint_rejects_unknown(litehorse_home: Path) -> None:
    state = StubState()
    await _dispatch("skill", ["nope"], state)
    assert state.pending_attachments == []
    assert any("no skill" in m for m in state.messages)


# ---------- structural guard (plan's "single source of truth") ----------


def test_slash_handlers_import_from_commands(litehorse_home: Path) -> None:
    """Each new slash handler must import its command counterpart.

    The plan bans duplicate implementations between REPL and scripted
    surfaces; this test fails if anyone copy-pastes logic instead of
    delegating.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    for slug in ("cron", "memory", "skills"):
        text = (root / "src/lite_horse/cli/repl/slash_handlers" / f"{slug}.py").read_text(
            encoding="utf-8"
        )
        assert f"from lite_horse.cli.commands import {slug}" in text, (
            f"slash_handlers/{slug}.py must import from commands/{slug}.py"
        )
