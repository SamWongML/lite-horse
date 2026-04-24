"""Tests for the tool-call panel and approval prompt."""
from __future__ import annotations

import pytest

from lite_horse.cli.repl import tool_ui as ui_mod
from lite_horse.cli.repl.tool_ui import (
    ApprovalDecision,
    ToolCallPanel,
    _first_lines,
    _looks_like_diff,
    _looks_like_json,
    _shorten,
    prompt_approval,
    render_tool_announce,
    render_tool_output,
)


def test_panel_announce_keeps_order() -> None:
    p = ToolCallPanel()
    p.announce("a", "{}")
    p.announce("b", "{}")
    assert [r.name for r in p.records] == ["a", "b"]


def test_panel_attach_output_pairs_with_pending() -> None:
    p = ToolCallPanel()
    a = p.announce("a", "{}")
    p.announce("b", "{}")
    matched = p.attach_output("a", "ok")
    assert matched is a
    assert a.output == "ok"


def test_panel_attach_output_no_match_returns_none() -> None:
    p = ToolCallPanel()
    p.announce("a", "{}")
    assert p.attach_output("z", "?") is None


def test_panel_expand_last() -> None:
    p = ToolCallPanel()
    p.announce("a", "{}")
    rec = p.expand_last()
    assert rec is not None and rec.expanded


def test_panel_enforces_max_records() -> None:
    p = ToolCallPanel(max_records=3)
    for i in range(5):
        p.announce(f"t{i}", "{}")
    assert [r.name for r in p.records] == ["t2", "t3", "t4"]


def test_shorten_elides_long() -> None:
    assert _shorten("abcdefghij", 5) == "abcd…"
    assert _shorten("abc", 5) == "abc"


def test_first_lines_truncates() -> None:
    text = "\n".join(str(i) for i in range(10))
    out = _first_lines(text, 3)
    assert out.count("\n") == 3  # 3 lines + the ellipsis suffix
    assert out.endswith("…")


def test_looks_like_diff_positive() -> None:
    assert _looks_like_diff("--- a\n+++ b\n@@ 1,2 @@\n+hi")


def test_looks_like_diff_negative() -> None:
    assert not _looks_like_diff("hello\nworld")


def test_looks_like_json_positive() -> None:
    assert _looks_like_json('{"a": 1}')
    assert _looks_like_json("[1, 2, 3]")


def test_looks_like_json_negative() -> None:
    assert not _looks_like_json("plain text")


def test_render_announce_returns_text_object() -> None:
    rec = ToolCallPanel().announce("mem", '{"action":"add"}')
    text = render_tool_announce(rec)
    # Rich Text exposes ``plain`` — use it so we don't depend on repr.
    assert rec.name in text.plain


def test_render_output_collapses_by_default() -> None:
    rec = ToolCallPanel().announce("mem", "{}")
    rec.output = "\n".join(f"line{i}" for i in range(30))
    panel = render_tool_output(rec, expanded=False)
    # Panel object is rich.panel.Panel — title carries the hint.
    assert "Ctrl-O" in panel.title


def test_render_output_expanded_drops_hint() -> None:
    rec = ToolCallPanel().announce("mem", "{}")
    rec.output = "short output"
    panel = render_tool_output(rec, expanded=True)
    assert "Ctrl-O" not in panel.title


@pytest.mark.asyncio
async def test_prompt_approval_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_prompt_async(self, question: str) -> str:
        _ = self, question
        return "y"

    from prompt_toolkit import PromptSession

    monkeypatch.setattr(PromptSession, "prompt_async", fake_prompt_async)
    decision = await prompt_approval("x", "y")
    assert decision is ApprovalDecision.YES


@pytest.mark.asyncio
async def test_prompt_approval_capital_a_means_always(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(self, q: str) -> str:
        _ = self, q
        return "A"

    from prompt_toolkit import PromptSession

    monkeypatch.setattr(PromptSession, "prompt_async", fake)
    assert await prompt_approval("x", "y") is ApprovalDecision.ALWAYS


@pytest.mark.asyncio
async def test_prompt_approval_capital_n_means_never(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(self, q: str) -> str:
        _ = self, q
        return "N"

    from prompt_toolkit import PromptSession

    monkeypatch.setattr(PromptSession, "prompt_async", fake)
    assert await prompt_approval("x", "y") is ApprovalDecision.NEVER


@pytest.mark.asyncio
async def test_prompt_approval_eof_defaults_no(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(self, q: str) -> str:
        _ = self, q
        raise EOFError

    from prompt_toolkit import PromptSession

    monkeypatch.setattr(PromptSession, "prompt_async", fake)
    assert await prompt_approval("x", "y") is ApprovalDecision.NO


def test_module_exports_stable() -> None:
    # Public names the REPL and tests consume.
    for name in (
        "ApprovalDecision",
        "ToolCallPanel",
        "prompt_approval",
        "render_tool_announce",
        "render_tool_output",
    ):
        assert hasattr(ui_mod, name)
