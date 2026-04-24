"""Unit tests for the picker. Radiolist path is mocked; fzf path is sandboxed."""
from __future__ import annotations

from typing import Any

import pytest

from lite_horse.cli.repl import picker as picker_mod
from lite_horse.cli.repl.picker import PickerItem, pick_one


@pytest.mark.asyncio
async def test_pick_one_empty_items_returns_none() -> None:
    assert await pick_one("title", []) is None


@pytest.mark.asyncio
async def test_pick_one_returns_radiolist_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_radiolist(title: str, items: list[PickerItem]) -> str | None:
        assert title == "pick"
        assert items[0].value == "a"
        return "a"

    monkeypatch.setattr(picker_mod, "_pick_with_radiolist", fake_radiolist)
    items = [PickerItem(value="a", label="A"), PickerItem(value="b", label="B")]
    result = await pick_one("pick", items)
    assert result == "a"


@pytest.mark.asyncio
async def test_pick_one_with_fzf_when_requested_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(picker_mod, "has_fzf", lambda: True)

    def fake_fzf(items: list[PickerItem]) -> str | None:
        return items[1].value

    monkeypatch.setattr(picker_mod, "_pick_with_fzf", fake_fzf)
    result = await pick_one(
        "pick",
        [PickerItem(value="x", label="X"), PickerItem(value="y", label="Y")],
        use_fzf=True,
    )
    assert result == "y"


@pytest.mark.asyncio
async def test_pick_one_fzf_fallback_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picker_mod, "has_fzf", lambda: False)

    async def radiolist_stub(title: str, items: list[PickerItem]) -> str | None:
        _ = title, items
        return "r"

    monkeypatch.setattr(picker_mod, "_pick_with_radiolist", radiolist_stub)
    result = await pick_one(
        "pick",
        [PickerItem(value="r", label="R")],
        use_fzf=True,
    )
    assert result == "r"


def test_pick_with_fzf_returns_none_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Completed:
        returncode = 130
        stdout = ""

    def fake_run(*args: Any, **kwargs: Any) -> _Completed:
        _ = args, kwargs
        return _Completed()

    monkeypatch.setattr(picker_mod.subprocess, "run", fake_run)
    result = picker_mod._pick_with_fzf([PickerItem(value="a", label="A")])
    assert result is None
