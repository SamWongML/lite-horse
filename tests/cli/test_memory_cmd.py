"""Tests for `litehorse memory {show, clear}`."""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lite_horse.cli.commands.memory import app
from lite_horse.memory.store import MemoryStore


def test_show_empty_memory(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["show"])
    assert result.exit_code == 0
    assert "(empty)" in result.stdout


def test_show_prints_entries(litehorse_home: Path) -> None:
    store = MemoryStore.for_memory()
    store.add("prefers kebab-case")
    store.add("uses pnpm, not npm")
    runner = CliRunner()
    result = runner.invoke(app, ["show"])
    assert result.exit_code == 0
    assert "prefers kebab-case" in result.stdout
    assert "uses pnpm, not npm" in result.stdout


def test_show_json_has_entries_array(litehorse_home: Path) -> None:
    store = MemoryStore.for_memory()
    store.add("likes rich tracebacks")
    runner = CliRunner()
    result = runner.invoke(app, ["show", "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["data"]["entries"] == ["likes rich tracebacks"]


def test_show_user_flag_switches_store(litehorse_home: Path) -> None:
    user_store = MemoryStore.for_user()
    user_store.add("timezone UTC+1")
    runner = CliRunner()
    result = runner.invoke(app, ["show", "--user"])
    assert result.exit_code == 0
    assert "timezone UTC+1" in result.stdout


def test_clear_wipes_entries(litehorse_home: Path) -> None:
    store = MemoryStore.for_memory()
    store.add("ephemeral")
    runner = CliRunner()
    result = runner.invoke(app, ["clear", "--yes"])
    assert result.exit_code == 0
    assert MemoryStore.for_memory().entries() == []


def test_clear_user_flag_does_not_touch_memory(litehorse_home: Path) -> None:
    mem = MemoryStore.for_memory()
    mem.add("agent note")
    usr = MemoryStore.for_user()
    usr.add("user note")
    runner = CliRunner()
    result = runner.invoke(app, ["clear", "--user", "--yes"])
    assert result.exit_code == 0
    assert MemoryStore.for_memory().entries() == ["agent note"]
    assert MemoryStore.for_user().entries() == []
