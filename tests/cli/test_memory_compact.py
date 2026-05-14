"""``litehorse memory compact`` reduces MEMORY.md utilisation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lite_horse.agent import consolidator as cons_mod
from lite_horse.cli.commands import memory as memory_cmd
from lite_horse.constants import MEMORY_CHAR_LIMIT
from lite_horse.memory.store import MemoryStore


class _FakeRunResult:
    def __init__(self, final_output: str) -> None:
        self.final_output = final_output


def _stub_runner(monkeypatch: pytest.MonkeyPatch, *, final_output: str) -> None:
    async def fake_run(*a: Any, **k: Any) -> _FakeRunResult:
        return _FakeRunResult(final_output=final_output)

    monkeypatch.setattr(cons_mod.Runner, "run", fake_run)


@pytest.mark.asyncio
async def test_compact_noop_when_under_threshold(litehorse_home: Path) -> None:
    del litehorse_home
    MemoryStore.for_memory().add("only one short fact")
    result = await memory_cmd._run_compact_async(model="gpt-test")
    assert result["status"] == "noop"
    assert result["reason"] == "under_threshold"


@pytest.mark.asyncio
async def test_compact_noop_on_empty_memory(litehorse_home: Path) -> None:
    del litehorse_home
    result = await memory_cmd._run_compact_async(model="gpt-test")
    assert result["status"] == "noop"
    assert result["reason"] == "empty"


@pytest.mark.asyncio
async def test_compact_shrinks_memory_under_threshold(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    store = MemoryStore.for_memory()
    # Fill past 80% utilisation with multiple bulky entries.
    for i in range(3):
        store.add(f"entry-{i}: " + "x" * 590)
    chars_before = store.total_chars()
    assert chars_before > MEMORY_CHAR_LIMIT * 0.8

    _stub_runner(
        monkeypatch,
        final_output='["compacted fact one", "compacted fact two"]',
    )
    result = await memory_cmd._run_compact_async(model="gpt-test")
    assert result["status"] == "compacted"
    assert int(result["after_chars"]) < chars_before
    entries = MemoryStore.for_memory().entries()
    assert entries == ["compacted fact one", "compacted fact two"]


@pytest.mark.asyncio
async def test_compact_noop_when_consolidator_returns_empty(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del litehorse_home
    store = MemoryStore.for_memory()
    for i in range(3):
        store.add(f"e-{i}: " + "y" * 590)

    _stub_runner(monkeypatch, final_output="[]")
    result = await memory_cmd._run_compact_async(model="gpt-test")
    assert result["status"] == "noop"
    assert result["reason"] == "consolidator_empty"
