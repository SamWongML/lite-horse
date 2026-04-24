"""Foundation slash commands: /help, /exit, /clear (+ aliases).

Later commits add /new, /resume, /fork, /compact, /share — they live in this
same file and share the registry built by :func:`build_default_registry`.
"""
from __future__ import annotations

from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)


async def _help(args: list[str], state: Any) -> SlashOutcome:
    registry: SlashRegistry | None = getattr(state, "registry", None)
    printer = getattr(state, "print_line", print)
    if registry is None:
        printer("(no slash registry attached to state)")
        return SlashOutcome.CONTINUE
    printer("slash commands:")
    for cmd in registry.all_commands():
        alias_hint = (
            f"  (aliases: {', '.join('/' + a for a in cmd.aliases)})"
            if cmd.aliases else ""
        )
        printer(f"  /{cmd.name:<10} {cmd.summary}{alias_hint}")
    return SlashOutcome.CONTINUE


async def _exit(args: list[str], state: Any) -> SlashOutcome:
    return SlashOutcome.EXIT


async def _clear(args: list[str], state: Any) -> SlashOutcome:
    return SlashOutcome.CLEAR


def build_default_registry() -> SlashRegistry:
    """Construct the registry with the foundation commands wired in.

    Subsequent commits append more commands by calling ``register`` on the
    same registry object before the loop starts.
    """
    reg = SlashRegistry()
    reg.register(SlashCommand(
        name="help",
        summary="show slash command reference",
        handler=_help,
        aliases=("h",),
    ))
    reg.register(SlashCommand(
        name="exit",
        summary="exit the REPL",
        handler=_exit,
        aliases=("quit", "q"),
    ))
    reg.register(SlashCommand(
        name="clear",
        summary="clear the scrollback",
        handler=_clear,
        aliases=("cls",),
    ))
    return reg
