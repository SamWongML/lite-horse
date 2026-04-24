"""Slash command registry, alias map, parser, and dispatcher.

Slash handlers are async callables receiving ``(args: list[str], state)`` where
``state`` is the mutable :class:`ReplState` (added in commit 3). For the
foundation phase, state is typed as ``Any`` to avoid a circular import.

A handler returns one of the :class:`SlashOutcome` values to signal what the
loop should do next:

- ``CONTINUE`` — handled in-band, redraw prompt
- ``CLEAR``    — clear the scrollback (Ctrl-L equivalent), then redraw
- ``EXIT``     — break the REPL loop and exit cleanly
"""
from __future__ import annotations

import enum
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


class SlashOutcome(enum.Enum):
    CONTINUE = "continue"
    CLEAR = "clear"
    EXIT = "exit"


SlashHandler = Callable[[list[str], Any], Awaitable[SlashOutcome]]


@dataclass(frozen=True)
class SlashCommand:
    name: str
    summary: str
    handler: SlashHandler
    aliases: tuple[str, ...] = ()


@dataclass
class SlashRegistry:
    """Registry of slash commands keyed by canonical name; aliases routed."""

    _commands: dict[str, SlashCommand] = field(default_factory=dict)
    _aliases: dict[str, str] = field(default_factory=dict)

    def register(self, cmd: SlashCommand) -> None:
        if cmd.name in self._commands:
            raise ValueError(f"slash command already registered: /{cmd.name}")
        for alias in cmd.aliases:
            existing = self._aliases.get(alias) or (alias if alias in self._commands else None)
            if existing is not None:
                raise ValueError(f"alias collision: /{alias} -> /{existing}")
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias] = cmd.name

    def resolve(self, name: str) -> SlashCommand | None:
        canonical = self._aliases.get(name, name)
        return self._commands.get(canonical)

    def all_commands(self) -> list[SlashCommand]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    def names(self) -> list[str]:
        """Canonical names + aliases, suitable for completion."""
        return sorted({*self._commands.keys(), *self._aliases.keys()})


@dataclass(frozen=True)
class ParsedSlash:
    name: str
    args: list[str]


def parse_slash(line: str) -> ParsedSlash | None:
    """Parse ``/cmd arg1 arg2`` into ``ParsedSlash``. Returns None if not slash.

    Empty slash (just ``/``) returns ``ParsedSlash(name="", args=[])`` so the
    caller can show a hint. Quoting follows shell rules via ``shlex``; if
    ``shlex`` fails (unbalanced quotes), fall back to whitespace split so the
    user isn't blocked from invoking simple commands.
    """
    stripped = line.lstrip()
    if not stripped.startswith("/"):
        return None
    body = stripped[1:].rstrip()
    if not body:
        return ParsedSlash(name="", args=[])
    try:
        parts = shlex.split(body)
    except ValueError:
        parts = body.split()
    if not parts:
        return ParsedSlash(name="", args=[])
    return ParsedSlash(name=parts[0], args=parts[1:])


async def dispatch(
    registry: SlashRegistry, parsed: ParsedSlash, state: Any
) -> tuple[SlashOutcome, str | None]:
    """Run the matching handler. Returns (outcome, error_message_or_None).

    A missing command returns ``(CONTINUE, "unknown command: /foo")`` so the
    loop can print the hint and stay in the REPL.
    """
    if not parsed.name:
        return SlashOutcome.CONTINUE, "empty slash command (try /help)"
    cmd = registry.resolve(parsed.name)
    if cmd is None:
        return SlashOutcome.CONTINUE, f"unknown command: /{parsed.name} (try /help)"
    outcome = await cmd.handler(parsed.args, state)
    return outcome, None
