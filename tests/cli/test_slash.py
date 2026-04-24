from __future__ import annotations

from typing import Any

import pytest

from lite_horse.cli.repl.slash import (
    ParsedSlash,
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
    dispatch,
    parse_slash,
)
from lite_horse.cli.repl.slash_handlers.session import build_default_registry


def test_parse_non_slash_returns_none() -> None:
    assert parse_slash("hello world") is None


def test_parse_simple() -> None:
    p = parse_slash("/help")
    assert p == ParsedSlash(name="help", args=[])


def test_parse_with_args() -> None:
    p = parse_slash("/resume abc-123 --foo")
    assert p == ParsedSlash(name="resume", args=["abc-123", "--foo"])


def test_parse_quoted_args() -> None:
    p = parse_slash('/attach "path with spaces.md"')
    assert p == ParsedSlash(name="attach", args=["path with spaces.md"])


def test_parse_unbalanced_quotes_falls_back() -> None:
    p = parse_slash('/foo "unbalanced')
    assert p is not None
    assert p.name == "foo"
    assert p.args == ['"unbalanced']


def test_parse_bare_slash_returns_empty_name() -> None:
    p = parse_slash("/")
    assert p == ParsedSlash(name="", args=[])


def test_parse_leading_whitespace_tolerated() -> None:
    p = parse_slash("   /exit")
    assert p == ParsedSlash(name="exit", args=[])


async def _noop(args: list[str], state: Any) -> SlashOutcome:
    return SlashOutcome.CONTINUE


def test_register_collision_rejected() -> None:
    reg = SlashRegistry()
    reg.register(SlashCommand(name="x", summary="", handler=_noop))
    with pytest.raises(ValueError, match="already registered"):
        reg.register(SlashCommand(name="x", summary="", handler=_noop))


def test_register_alias_collision_rejected() -> None:
    reg = SlashRegistry()
    reg.register(SlashCommand(name="exit", summary="", handler=_noop, aliases=("q",)))
    with pytest.raises(ValueError, match="alias collision"):
        reg.register(SlashCommand(name="quit", summary="", handler=_noop, aliases=("q",)))


def test_resolve_alias() -> None:
    reg = build_default_registry()
    assert reg.resolve("q") is reg.resolve("exit")
    assert reg.resolve("h") is reg.resolve("help")
    assert reg.resolve("cls") is reg.resolve("clear")


def test_names_includes_aliases() -> None:
    reg = build_default_registry()
    names = reg.names()
    for n in ("help", "h", "exit", "quit", "q", "clear", "cls"):
        assert n in names


async def test_dispatch_unknown() -> None:
    reg = SlashRegistry()
    outcome, err = await dispatch(reg, ParsedSlash(name="ghost", args=[]), state=None)
    assert outcome is SlashOutcome.CONTINUE
    assert err is not None and "unknown" in err


async def test_dispatch_empty() -> None:
    reg = SlashRegistry()
    outcome, err = await dispatch(reg, ParsedSlash(name="", args=[]), state=None)
    assert outcome is SlashOutcome.CONTINUE
    assert err is not None and "empty" in err


async def test_dispatch_exit() -> None:
    reg = build_default_registry()
    outcome, err = await dispatch(reg, ParsedSlash(name="q", args=[]), state=None)
    assert outcome is SlashOutcome.EXIT
    assert err is None


async def test_dispatch_clear() -> None:
    reg = build_default_registry()
    outcome, err = await dispatch(reg, ParsedSlash(name="cls", args=[]), state=None)
    assert outcome is SlashOutcome.CLEAR
    assert err is None


async def test_dispatch_help_uses_state_printer() -> None:
    reg = build_default_registry()
    lines: list[str] = []

    class State:
        registry = reg
        print_line = staticmethod(lines.append)

    outcome, err = await dispatch(reg, ParsedSlash(name="help", args=[]), state=State())
    assert outcome is SlashOutcome.CONTINUE
    assert err is None
    body = "\n".join(lines)
    assert "/help" in body
    assert "/exit" in body
    assert "/clear" in body
    # alias hint rendered for /help (alias h)
    assert "/h" in body
