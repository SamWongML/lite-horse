"""`/memory [show|clear]` slash commands.

Both subcommands import their behavior from
:mod:`lite_horse.cli.commands.memory` so scripted and REPL surfaces read /
mutate the same stores through one code path.
"""
from __future__ import annotations

from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)


async def _memory(args: list[str], state: Any) -> SlashOutcome:
    printer = getattr(state, "print_line", print)

    user = False
    filtered: list[str] = []
    for tok in args:
        if tok in ("--user", "-u"):
            user = True
        else:
            filtered.append(tok)

    sub = filtered[0].lower() if filtered else "show"
    if sub not in ("show", "clear"):
        printer(f"[memory] unknown subcommand: {sub!r} (try: show | clear [--user])")
        return SlashOutcome.CONTINUE

    from lite_horse.cli.commands import memory as memory_cmd

    if sub == "show":
        data = memory_cmd.read_entries(user=user)
        entries = data["entries"]
        assert isinstance(entries, list)
        if not entries:
            printer(f"[{data['label']}] (empty)")
            return SlashOutcome.CONTINUE
        for e in entries:
            printer(f"  • {e}")
        printer(f"[{data['label']}] {data['total_chars']}/{data['char_limit']} chars, "
                f"{len(entries)} entries")
        return SlashOutcome.CONTINUE

    # clear
    removed = memory_cmd.clear_entries(user=user)
    label = "USER.md" if user else "MEMORY.md"
    printer(f"[memory] cleared {removed} entries from {label}")
    return SlashOutcome.CONTINUE


def register(reg: SlashRegistry) -> None:
    reg.register(SlashCommand(
        name="memory",
        summary="show or clear MEMORY.md / USER.md",
        handler=_memory,
    ))
