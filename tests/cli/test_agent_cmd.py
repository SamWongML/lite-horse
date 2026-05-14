"""CLI parity gate — ``litehorse agent {ls, create, use, show}``.

Drives the helpers in :mod:`lite_horse.cli.commands.agent` directly so
the test stays fast and LLM-free. Asserts the local layout
(``~/.litehorse/agents/<slug>/``) plus the active-agent file mirror what
the cloud ``agents`` table would express.

The full Click round-trip (Typer → Click) is exercised in the broader
``test_app_dispatch`` suite; here we focus on the on-disk byte shape so
drift in the local layout fails CI fast.
"""
from __future__ import annotations

from pathlib import Path

from lite_horse.cli.commands.agent import (
    create_local_agent,
    current_agent,
    ensure_default_layout,
    list_local_agents,
    set_current_agent,
)


def test_ensure_default_layout_creates_dir(litehorse_home: Path) -> None:
    ensure_default_layout()
    assert (litehorse_home / "agents" / "default").is_dir()
    assert current_agent() == "default"


def test_create_local_agent_seeds_files(litehorse_home: Path) -> None:
    ensure_default_layout()
    home = create_local_agent("coder", persona="senior engineer")
    assert home == litehorse_home / "agents" / "coder"
    assert (home / "memory.md").read_text() == ""
    assert (home / "user.md").read_text() == ""
    assert (home / "skills").is_dir()
    assert (home / "persona.txt").read_text() == "senior engineer"


def test_use_persists_active_agent(litehorse_home: Path) -> None:
    ensure_default_layout()
    create_local_agent("shopper", persona="")
    set_current_agent("shopper")
    assert current_agent() == "shopper"
    assert (litehorse_home / "current_agent").read_text() == "shopper"


def test_env_override_beats_file(
    litehorse_home: Path, monkeypatch: object
) -> None:
    ensure_default_layout()
    set_current_agent("default")
    # type-ignored monkeypatch arg — pytest passes the right type at runtime.
    monkeypatch.setenv("LITEHORSE_AGENT", "writer")  # type: ignore[attr-defined]
    assert current_agent() == "writer"


def test_list_local_agents_marks_current(litehorse_home: Path) -> None:
    ensure_default_layout()
    create_local_agent("coder")
    create_local_agent("shopper")
    set_current_agent("shopper")
    rows = list_local_agents()
    by_slug = {r["slug"]: r for r in rows}
    assert {"default", "coder", "shopper"} <= set(by_slug)
    assert by_slug["shopper"]["is_current"] is True
    assert by_slug["default"]["is_current"] is False
