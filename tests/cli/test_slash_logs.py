"""Phase 30 ``/logs`` slash-command behavior."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lite_horse.cli.repl.slash import ParsedSlash, dispatch
from lite_horse.cli.repl.slash_handlers.session import build_default_registry


@dataclass
class StubState:
    messages: list[str] = field(default_factory=list)
    registry: Any = None

    def print_line(self, msg: str) -> None:
        self.messages.append(msg)


async def _run(args: list[str], state: StubState) -> None:
    reg = build_default_registry()
    state.registry = reg
    await dispatch(reg, ParsedSlash(name="logs", args=args), state)


async def test_logs_reports_when_file_missing(litehorse_home: Path) -> None:
    state = StubState()
    await _run([], state)
    assert any("no lines" in m for m in state.messages)


async def test_logs_rejects_non_numeric_arg(litehorse_home: Path) -> None:
    state = StubState()
    await _run(["forty"], state)
    assert any("not a number" in m for m in state.messages)


async def test_logs_pages_recent_lines_when_present(
    litehorse_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (litehorse_home / "litehorse.log").write_text(
        "\n".join(f"entry{i}" for i in range(5)) + "\n",
        encoding="utf-8",
    )
    paged: list[list[str]] = []
    monkeypatch.setattr(
        "lite_horse.cli.repl.slash_handlers.tools._display_in_pager",
        lambda lines: paged.append(list(lines)),
    )
    state = StubState()
    await _run(["3"], state)
    assert paged == [["entry2", "entry3", "entry4"]]


async def test_logs_caps_n_to_reasonable_bound(
    litehorse_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (litehorse_home / "litehorse.log").write_text(
        "\n".join(f"line{i}" for i in range(100)) + "\n",
        encoding="utf-8",
    )
    paged: list[list[str]] = []
    monkeypatch.setattr(
        "lite_horse.cli.repl.slash_handlers.tools._display_in_pager",
        lambda lines: paged.append(list(lines)),
    )
    state = StubState()
    await _run(["100000"], state)
    # All 100 lines present since cap is 5000 and we only wrote 100.
    assert paged and len(paged[0]) == 100
