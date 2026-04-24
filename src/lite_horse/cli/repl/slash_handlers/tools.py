"""General REPL slash commands.

- ``/verbose [off|new|all]`` — tool-call display level (consumed by the
  renderer in Phase 29).
- ``/usage`` / ``/cost`` — token + cost meter for the current session.
- ``/abort`` — cancel the in-flight agent turn (equivalent to Ctrl-C #1).
- ``/logs`` — stub that points at ``litehorse logs`` for now; Phase 30
  wires the in-REPL pager.
- ``/editor`` — compose the next prompt in ``$EDITOR`` and stash it on
  ``state.pending_attachments`` until the next turn.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)

_VALID_VERBOSE = frozenset({"off", "new", "all"})


async def _verbose(args: list[str], state: Any) -> SlashOutcome:
    printer = getattr(state, "print_line", print)
    if not args:
        printer(f"[verbose] {state.verbose} (try off | new | all)")
        return SlashOutcome.CONTINUE
    target = args[0].strip().lower()
    if target not in _VALID_VERBOSE:
        printer(f"[verbose] unknown level: {args[0]!r} (try off | new | all)")
        return SlashOutcome.CONTINUE
    state.verbose = target
    printer(f"[verbose] {target}")
    return SlashOutcome.CONTINUE


async def _usage(args: list[str], state: Any) -> SlashOutcome:
    printer = getattr(state, "print_line", print)
    pct = 0.0
    if state.ctx_max:
        pct = (state.total_tokens / state.ctx_max) * 100.0
    cost = "—" if state.total_cost_usd is None else f"${state.total_cost_usd:.4f}"
    printer(
        f"[usage] tokens: {state.total_tokens:,} / {state.ctx_max:,} "
        f"({pct:.1f}%)   cost: {cost}"
    )
    return SlashOutcome.CONTINUE


async def _abort(args: list[str], state: Any) -> SlashOutcome:
    printer = getattr(state, "print_line", print)
    task = getattr(state, "current_turn_task", None)
    if task is None or task.done():
        printer("[abort] no turn in flight")
        return SlashOutcome.CONTINUE
    task.cancel()
    printer("[abort] cancel requested")
    return SlashOutcome.CONTINUE


_DEFAULT_LOG_TAIL = 50


async def _logs(args: list[str], state: Any) -> SlashOutcome:
    """Tail the stderr log file into a pager overlay (Phase 30).

    ``/logs`` tails the default 50 lines; ``/logs N`` tails ``N`` (1-5000).
    Output is piped through ``rich.Console.pager`` when stdout is a TTY
    (Esc / ``q`` to close, matching ``less``), and falls back to plain
    lines otherwise.
    """
    from lite_horse.cli.commands.logs import log_path, tail_lines

    printer = getattr(state, "print_line", print)

    n = _DEFAULT_LOG_TAIL
    if args:
        try:
            n = int(args[0])
        except ValueError:
            printer(f"[logs] not a number: {args[0]!r}")
            return SlashOutcome.CONTINUE
    n = min(max(1, n), 5_000)

    lines = tail_lines(n=n)
    if not lines:
        printer(f"[logs] no lines yet — log file: {log_path()}")
        return SlashOutcome.CONTINUE

    _display_in_pager(lines)
    return SlashOutcome.CONTINUE


def _display_in_pager(lines: list[str]) -> None:
    """Page the given lines via rich; fall back to plain print on non-TTY."""
    import sys

    if not sys.stdout.isatty():
        for line in lines:
            print(line)
        return

    from rich.console import Console

    console = Console()
    with console.pager(styles=False):
        for line in lines:
            console.print(line, soft_wrap=True, highlight=False)


async def _editor(args: list[str], state: Any) -> SlashOutcome:
    """Open ``$EDITOR`` on a temp file; stash the result as a pending prompt.

    Falls back to ``vi`` when ``$EDITOR`` is unset. The composed buffer is
    consumed by the REPL loop on the next iteration via
    ``state.pending_attachments`` — treated as a text attachment so it is
    prepended verbatim to the next user turn without re-invoking the
    prompt box.
    """
    printer = getattr(state, "print_line", print)
    editor = os.environ.get("EDITOR") or "vi"

    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", prefix="litehorse-editor-", delete=False
    ) as f:
        path = f.name
    try:
        rc = subprocess.call([editor, path])
        if rc != 0:
            printer(f"[editor] aborted (exit {rc})")
            return SlashOutcome.CONTINUE
        with open(path, encoding="utf-8") as f:
            content = f.read().strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if not content:
        printer("[editor] empty buffer — nothing staged")
        return SlashOutcome.CONTINUE

    state.pending_attachments.append({"kind": "text", "content": content})
    printer(f"[editor] {len(content)} chars staged for the next turn")
    return SlashOutcome.CONTINUE


def register(reg: SlashRegistry) -> None:
    reg.register(SlashCommand(
        name="verbose",
        summary="tool-call display level (off | new | all)",
        handler=_verbose,
    ))
    reg.register(SlashCommand(
        name="usage",
        summary="show token + cost meter",
        handler=_usage,
        aliases=("cost",),
    ))
    reg.register(SlashCommand(
        name="abort",
        summary="cancel the in-flight agent turn",
        handler=_abort,
    ))
    reg.register(SlashCommand(
        name="logs",
        summary="tail stderr log (pager; `/logs N` for N lines)",
        handler=_logs,
    ))
    reg.register(SlashCommand(
        name="editor",
        summary="compose the next prompt in $EDITOR",
        handler=_editor,
    ))
