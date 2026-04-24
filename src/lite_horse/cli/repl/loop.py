"""Async REPL loop — reads a line, dispatches slash-or-chat, renders, repeats.

Three entry shapes:

- ``main_loop()`` — interactive REPL (stdin TTY).
- ``main_loop(prompt="text")`` — one-shot: stream the response and exit.
- ``main_loop(stdin_text="text")`` — one-shot from piped stdin (no TTY).

The function is ``async def`` and assumes ``asyncio.run`` is called by the
Click command body in ``cli.app``. Heavy imports stay inside the function
bodies to keep ``litehorse --help`` fast.
"""
from __future__ import annotations

import asyncio
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from lite_horse.cli._tty import detect
from lite_horse.cli.repl.slash import SlashOutcome, dispatch, parse_slash
from lite_horse.cli.repl.slash_handlers.session import build_default_registry
from lite_horse.cli.repl.stream_buf import StreamAssembler
from lite_horse.core.session_key import build_session_key

# Window in seconds during which a second Ctrl-C exits the REPL after the
# first cancelled the in-flight turn. Matches the spec in
# docs/plans/v0.3-cli-entrypoint.md.
CTRL_C_EXIT_WINDOW_S = 2.0

# Default context-window denominator for the toolbar. Per-model lookup
# arrives in a follow-up phase; the displayed pct is honest about being a
# rough cap, not a hard model truth.
DEFAULT_CTX_MAX = 200_000


@dataclass
class ReplState:
    """Mutable state threaded through slash handlers and the loop body.

    ``permission_mode`` mirrors the active policy registered under the
    session key — slash handlers and the toolbar read from here so the
    display and the ``core.permission`` registry never disagree.
    """

    session_key: str
    model: str = "—"
    permission_mode: str = "auto"
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)
    debug: bool = False
    verbose: str = "new"  # off | new | all
    total_tokens: int = 0
    ctx_max: int = DEFAULT_CTX_MAX
    total_cost_usd: float | None = None
    registry: Any = None  # SlashRegistry; Any to avoid import cycle
    print_line: Any = print
    pending_attachments: list[Any] = field(default_factory=list)
    current_turn_task: Any = None  # asyncio.Task | None
    expand_last_tool: bool = False  # toggled by Ctrl-O


def _default_session_key() -> str:
    return build_session_key(platform="cli", chat_type="repl", chat_id="local")


def _load_model_name() -> str:
    """Best-effort load of the configured model. Falls back to ``'?'``."""
    try:
        from lite_horse.config import load_config

        return load_config().model
    except Exception:
        return "?"


def _print_status_line(state: ReplState) -> None:
    """Emit a one-liner above the Live block that bridges the toolbar gap.

    Goes to stderr so it doesn't pollute the chat stream when piped.
    """
    from lite_horse.cli.repl.toolbar import format_toolbar

    line = format_toolbar(
        model=state.model,
        session_key=state.session_key,
        total_tokens=state.total_tokens,
        ctx_max=state.ctx_max,
        cost_usd=state.total_cost_usd,
        permission_mode=state.permission_mode,
    )
    print(f"  {line}", file=sys.stderr, flush=True)


async def _run_one_turn(state: ReplState, user_text: str) -> None:
    """Stream one turn to the renderer chosen for the current TTY.

    Flushes ``state.pending_attachments`` into the user message as a
    ``<attachment>...</attachment>`` prefix block so the model sees any
    staged files / URLs / images before the prose.
    """
    from lite_horse.api import (
        StreamDelta,
        StreamDone,
        StreamToolCall,
        StreamToolOutput,
        run_turn_streaming,
    )
    from lite_horse.cli.repl.attachments import format_attachments_for_turn
    from lite_horse.cli.repl.renderer import make_renderer
    from lite_horse.cli.repl.tool_ui import (
        ToolCallPanel,
        render_tool_announce,
        render_tool_output,
    )

    tty = detect()
    renderer = make_renderer(use_color=tty.use_color, stdout_tty=tty.stdout_tty)
    asm = StreamAssembler()
    panel = ToolCallPanel()

    attachments = list(state.pending_attachments)
    state.pending_attachments.clear()
    if attachments:
        prefix = format_attachments_for_turn(attachments)
        user_text = prefix + user_text

    if tty.stdout_tty:
        _print_status_line(state)
    renderer.start()
    asm.mark_box_opened()
    try:
        async for ev in run_turn_streaming(
            session_key=state.session_key, user_text=user_text, source="cli"
        ):
            if isinstance(ev, StreamDelta):
                asm.feed(ev.text)
                renderer.update(asm.text)
            elif isinstance(ev, StreamToolCall):
                announced = panel.announce(ev.name, ev.arguments)
                _emit_tool_announce(announced, render_tool_announce, tty.use_color)
            elif isinstance(ev, StreamToolOutput):
                finished = panel.attach_output(ev.name, ev.output)
                if finished is not None:
                    expanded = state.verbose == "all" or state.expand_last_tool
                    _emit_tool_output(finished, render_tool_output, expanded,
                                      state.verbose, tty.use_color)
            elif isinstance(ev, StreamDone):
                final = asm.finalize(ev.result.final_output)
                renderer.update(final)
                if ev.result.total_tokens is not None:
                    state.total_tokens += ev.result.total_tokens
    finally:
        renderer.stop()
        # Reset one-shot expansion so the next tool call starts collapsed.
        state.expand_last_tool = False


def _emit_tool_announce(rec: Any, render: Any, use_color: bool) -> None:
    """Print the dim announce line to stderr (chat stream is on stdout)."""
    if use_color:
        from rich.console import Console

        Console(stderr=True).print(render(rec))
    else:
        print(f"→ {rec.name}  {rec.arguments[:80]}",
              file=sys.stderr, flush=True)


def _emit_tool_output(
    rec: Any, render: Any, expanded: bool, verbose: str, use_color: bool
) -> None:
    if verbose == "off":
        return
    if use_color:
        from rich.console import Console

        Console(stderr=True).print(render(rec, expanded=expanded))
    else:
        body = rec.output or ""
        if not expanded:
            lines = body.splitlines()
            if len(lines) > 6:
                body = "\n".join(lines[:6]) + "\n…"
        print(f"↩ {rec.name}\n{body}", file=sys.stderr, flush=True)


@dataclass
class _CtrlCState:
    last_press: float = 0.0
    requested_exit: bool = False


def _make_sigint_handler(
    cc: _CtrlCState, task: asyncio.Task[Any], *, now: Any = time.monotonic
) -> Any:
    """Build a SIGINT callback implementing the two-press protocol.

    Pure factory so the behaviour is unit-testable without raising real
    signals. ``now`` is injectable for deterministic tests.
    """

    def _handler() -> None:
        t = now()
        if cc.last_press and (t - cc.last_press) < CTRL_C_EXIT_WINDOW_S:
            cc.requested_exit = True
        cc.last_press = t
        if not task.done():
            task.cancel()

    return _handler


async def _await_with_two_press_ctrl_c(
    state: ReplState, user_text: str
) -> bool:
    """Run one turn, honoring two-press Ctrl-C cancel-then-exit.

    Returns ``True`` if the user requested exit (second press inside the
    2 s window), ``False`` otherwise.
    """
    loop = asyncio.get_running_loop()
    cc = _CtrlCState()
    task: asyncio.Task[None] = asyncio.create_task(_run_one_turn(state, user_text))
    state.current_turn_task = task
    handler = _make_sigint_handler(cc, task)

    installed = False
    try:
        try:
            loop.add_signal_handler(signal.SIGINT, handler)
            installed = True
        except NotImplementedError:
            # Windows: graceful degradation — single press still raises
            # KeyboardInterrupt; second-press exit unavailable.
            pass

        try:
            await task
        except asyncio.CancelledError:
            print("[cancelled]", file=sys.stderr)
        except KeyboardInterrupt:
            cc.requested_exit = True
        except Exception as exc:
            print(f"[error: {exc}]", file=sys.stderr)
    finally:
        if installed:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except NotImplementedError:
                pass
        state.current_turn_task = None

    return cc.requested_exit


async def _interactive_loop(state: ReplState) -> int:
    """Drive a prompt_toolkit PromptSession until /exit or Ctrl-D."""
    from prompt_toolkit.patch_stdout import patch_stdout

    from lite_horse.cli.repl.session import build_prompt_session
    from lite_horse.cli.repl.toolbar import build_bottom_toolbar

    assert state.registry is not None
    session = build_prompt_session(
        state.registry,
        bottom_toolbar=build_bottom_toolbar(state),
        state=state,
    )
    print("litehorse — type /help for commands, Esc-Enter to submit, Ctrl-D to exit",
          file=sys.stderr)
    while True:
        try:
            with patch_stdout():
                line = await session.prompt_async(">>> ")
        except (EOFError, KeyboardInterrupt):
            print("[exit]", file=sys.stderr)
            return 0
        line = line.rstrip()
        if not line:
            continue
        parsed = parse_slash(line)
        if parsed is not None:
            outcome, err = await dispatch(state.registry, parsed, state)
            if err:
                print(err, file=sys.stderr)
            if outcome is SlashOutcome.EXIT:
                return 0
            if outcome is SlashOutcome.CLEAR:
                # ANSI clear-screen; fall back to noop on dumb terminals.
                print("\x1b[2J\x1b[H", end="", flush=True)
            continue
        _auto_attach_from_line(state, line)
        if await _await_with_two_press_ctrl_c(state, line):
            print("[exit]", file=sys.stderr)
            return 0


def _auto_attach_from_line(state: ReplState, line: str) -> None:
    """Auto-stage ``@file`` / ``@url`` tokens found in a typed user line.

    Additions go on top of anything ``/attach`` already staged; duplicates
    (same path/url) are dropped so a line echoing an earlier /attach
    doesn't double-inject.
    """
    from lite_horse.cli.repl.attachments import detect_attachments

    new_atts = detect_attachments(line)
    if not new_atts:
        return
    existing = {
        att.get("path") or att.get("url")
        for att in state.pending_attachments
    }
    for att in new_atts:
        key = att.get("path") or att.get("url")
        if key in existing:
            continue
        state.pending_attachments.append(att)
        existing.add(key)


async def main_loop(
    *,
    prompt: str | None = None,
    stdin_text: str | None = None,
    session_key: str | None = None,
) -> int:
    """REPL entry point.

    - ``prompt`` set → one-shot, stream once, exit.
    - ``stdin_text`` set → one-shot using the piped buffer.
    - neither → interactive REPL.
    """
    state = ReplState(
        session_key=session_key or _default_session_key(),
        model=_load_model_name(),
        registry=build_default_registry(),
    )

    one_shot = prompt or stdin_text
    if one_shot is not None:
        try:
            await _run_one_turn(state, one_shot)
            return 0
        except Exception as exc:
            print(f"[error: {exc}]", file=sys.stderr)
            return 1

    return await _interactive_loop(state)
