"""Unit tests for Phase 28 slash handlers — session / model / tools groups.

We stub ``lite_horse.api._ensure_ready`` out of these tests so the handlers
exercise their branches without spinning up a real Agent / SessionDB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pytest

import lite_horse.api as api_mod
from lite_horse.cli.repl.slash import ParsedSlash, SlashOutcome, dispatch
from lite_horse.cli.repl.slash_handlers.session import (
    _fresh_cli_session_key,
    build_default_registry,
)


@dataclass
class StubState:
    session_key: str = "agent:main:cli:repl:local"
    model: str = "m-test"
    permission_mode: str = "auto"
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    debug: bool = False
    verbose: str = "new"
    total_tokens: int = 42
    ctx_max: int = 200_000
    total_cost_usd: float | None = None
    pending_attachments: list[Any] = field(default_factory=list)
    current_turn_task: Any = None
    registry: Any = None
    messages: list[str] = field(default_factory=list)
    expand_last_tool: bool = False

    def print_line(self, msg: str) -> None:
        self.messages.append(msg)


class StubDB:
    def __init__(self) -> None:
        self.created: list[tuple[str, str | None]] = []
        self.copied: list[tuple[str, str]] = []
        self._sessions: list[dict[str, Any]] = []
        self._prefix_map: dict[str, str | None] = {}
        self._prefix_ambiguous: set[str] = set()
        self._messages: dict[str, list[Any]] = {}

    # Match SessionDB interface used by handlers.
    def create_session(self, *, session_id: str, source: str = "cli",
                       model: str | None = None, **_: Any) -> None:
        _ = source
        self.created.append((session_id, model))

    def copy_messages(self, *, src_session_id: str, dst_session_id: str) -> int:
        self.copied.append((src_session_id, dst_session_id))
        return 3

    def list_recent_sessions(self, *, limit: int = 20,
                             include_ended: bool = True) -> list[dict[str, Any]]:
        _ = limit, include_ended
        return self._sessions

    def find_session_by_prefix(self, prefix: str) -> str | None:
        if prefix in self._prefix_ambiguous:
            raise ValueError(f"ambiguous session prefix: {prefix!r}")
        return self._prefix_map.get(prefix)

    def get_messages(self, session_key: str) -> list[Any]:
        return self._messages.get(session_key, [])


class StubCfg:
    model = "m-test"


@pytest.fixture
def stub_ensure_ready(monkeypatch: pytest.MonkeyPatch) -> StubDB:
    db = StubDB()

    async def fake() -> tuple[StubDB, Any, StubCfg]:
        return db, None, StubCfg()

    monkeypatch.setattr(api_mod, "_ensure_ready", fake)
    return db


@pytest.mark.asyncio
async def test_new_rotates_session_and_carries_permission(
    stub_ensure_ready: StubDB,
) -> None:
    _ = stub_ensure_ready
    from lite_horse.core.permission import get_policy, set_policy

    reg = build_default_registry()
    s = StubState(permission_mode="ro")
    set_policy(s.session_key, _policy("ro"))
    old = s.session_key
    outcome, err = await dispatch(reg, ParsedSlash(name="new", args=[]), s)
    assert err is None
    assert outcome is SlashOutcome.CLEAR
    assert s.session_key != old
    carried = get_policy(s.session_key)
    assert carried is not None
    assert carried.mode == "ro"


@pytest.mark.asyncio
async def test_resume_by_prefix_switches_session(
    stub_ensure_ready: StubDB,
) -> None:
    stub_ensure_ready._prefix_map["abc"] = "agent:main:cli:repl:local-abcdef"
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="resume", args=["abc"]), s)
    assert outcome is SlashOutcome.CLEAR
    assert s.session_key == "agent:main:cli:repl:local-abcdef"


@pytest.mark.asyncio
async def test_resume_unknown_prefix_prints_and_continues(
    stub_ensure_ready: StubDB,
) -> None:
    stub_ensure_ready._prefix_map = {}
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="resume", args=["zzz"]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("no session matches" in m for m in s.messages)


@pytest.mark.asyncio
async def test_resume_empty_list_prints_and_continues(
    stub_ensure_ready: StubDB,
) -> None:
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="resume", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("no sessions on record" in m for m in s.messages)


@pytest.mark.asyncio
async def test_fork_copies_and_switches(stub_ensure_ready: StubDB) -> None:
    from lite_horse.core.permission import get_policy

    reg = build_default_registry()
    s = StubState(permission_mode="auto")
    src = s.session_key
    outcome, _ = await dispatch(reg, ParsedSlash(name="fork", args=[]), s)
    assert outcome is SlashOutcome.CLEAR
    assert s.session_key != src
    assert stub_ensure_ready.created[0][0] == s.session_key
    assert stub_ensure_ready.copied == [(src, s.session_key)]
    # Permission carried over on the destination key
    assert get_policy(s.session_key) is not None


@pytest.mark.asyncio
async def test_compact_empty_session(stub_ensure_ready: StubDB) -> None:
    _ = stub_ensure_ready
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="compact", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("no messages" in m for m in s.messages)


@pytest.mark.asyncio
async def test_compact_invokes_consolidator(
    monkeypatch: pytest.MonkeyPatch, stub_ensure_ready: StubDB,
) -> None:
    stub_ensure_ready._messages = {
        "agent:main:cli:repl:local": [{"role": "user", "content": "hi"}],
    }

    class FakeConsolidator:
        def __init__(self, *, model: str) -> None:
            self.model = model

        async def run(self, *, turn_input: list[Any]) -> list[str]:
            _ = turn_input
            return ["user prefers terse replies"]

    class FakeMemStore:
        added: list[str] = []

        def add(self, entry: str) -> None:
            FakeMemStore.added.append(entry)

    import lite_horse.agent.consolidator as cons_mod
    import lite_horse.memory.store as mem_mod
    from lite_horse.cli.repl.slash_handlers import session as session_mod

    _ = session_mod
    monkeypatch.setattr(cons_mod, "Consolidator", FakeConsolidator)

    def _fake_for_memory() -> FakeMemStore:
        return FakeMemStore()

    monkeypatch.setattr(mem_mod.MemoryStore, "for_memory", staticmethod(_fake_for_memory))

    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="compact", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert FakeMemStore.added == ["user prefers terse replies"]
    assert any("1 entry added" in m for m in s.messages)


@pytest.mark.asyncio
async def test_model_no_args_shows_current(stub_ensure_ready: StubDB) -> None:
    _ = stub_ensure_ready

    from lite_horse.cli.repl import picker as picker_mod

    async def cancelled_pick(title: str, items: list[Any]) -> str | None:
        _ = title, items
        return None

    import pytest as _pytest

    mp = _pytest.MonkeyPatch()
    mp.setattr(picker_mod, "pick_one", cancelled_pick)
    try:
        reg = build_default_registry()
        s = StubState(model="m-active")
        outcome, _err = await dispatch(reg, ParsedSlash(name="model", args=[]), s)
        assert outcome is SlashOutcome.CONTINUE
    finally:
        mp.undo()


@pytest.mark.asyncio
async def test_model_switch_by_arg() -> None:
    reg = build_default_registry()
    s = StubState(model="m-old")
    outcome, _ = await dispatch(reg, ParsedSlash(name="model", args=["m-new"]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert s.model == "m-new"


@pytest.mark.asyncio
async def test_permission_switch_updates_state_and_registry() -> None:
    from lite_horse.core.permission import get_policy

    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="permission", args=["ro"]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert s.permission_mode == "ro"
    p = get_policy(s.session_key)
    assert p is not None and p.mode == "ro"


@pytest.mark.asyncio
async def test_permission_unknown_mode_prints_hint() -> None:
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="permission", args=["weird"]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("unknown mode" in m for m in s.messages)


@pytest.mark.asyncio
async def test_permission_read_only_alias_maps_to_ro() -> None:
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="permission", args=["read-only"]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert s.permission_mode == "ro"


@pytest.mark.asyncio
async def test_debug_toggles_root_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = monkeypatch
    reg = build_default_registry()
    s = StubState()
    try:
        outcome, _ = await dispatch(reg, ParsedSlash(name="debug", args=["on"]), s)
        assert outcome is SlashOutcome.CONTINUE
        assert s.debug is True
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(logging.WARNING)


@pytest.mark.asyncio
async def test_verbose_levels() -> None:
    reg = build_default_registry()
    s = StubState()
    await dispatch(reg, ParsedSlash(name="verbose", args=["all"]), s)
    assert s.verbose == "all"
    await dispatch(reg, ParsedSlash(name="verbose", args=["off"]), s)
    assert s.verbose == "off"
    await dispatch(reg, ParsedSlash(name="verbose", args=["bogus"]), s)
    # Bogus doesn't clobber state
    assert s.verbose == "off"


@pytest.mark.asyncio
async def test_usage_prints_counters() -> None:
    reg = build_default_registry()
    s = StubState(total_tokens=1234, ctx_max=200_000)
    outcome, _ = await dispatch(reg, ParsedSlash(name="usage", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("1,234" in m and "tokens" in m for m in s.messages)


@pytest.mark.asyncio
async def test_cost_is_alias_for_usage() -> None:
    reg = build_default_registry()
    s = StubState(total_tokens=5)
    outcome, _ = await dispatch(reg, ParsedSlash(name="cost", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("tokens" in m for m in s.messages)


@pytest.mark.asyncio
async def test_abort_no_task_in_flight() -> None:
    reg = build_default_registry()
    s = StubState()
    outcome, _ = await dispatch(reg, ParsedSlash(name="abort", args=[]), s)
    assert outcome is SlashOutcome.CONTINUE
    assert any("no turn in flight" in m for m in s.messages)


@pytest.mark.asyncio
async def test_abort_cancels_in_flight_task() -> None:
    import asyncio

    async def sleeper() -> None:
        await asyncio.sleep(5)

    task: asyncio.Task[None] = asyncio.create_task(sleeper())
    reg = build_default_registry()
    s = StubState(current_turn_task=task)
    await dispatch(reg, ParsedSlash(name="abort", args=[]), s)
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_fresh_cli_session_key_is_unique() -> None:
    keys = {_fresh_cli_session_key() for _ in range(5)}
    assert len(keys) == 5


def _policy(mode: str) -> Any:
    from lite_horse.core.permission import PermissionPolicy
    return PermissionPolicy(mode=mode)
