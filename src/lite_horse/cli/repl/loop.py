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

    Phase 27 fields wired here; Phase 28 will add ``allowed_tools`` /
    ``denied_tools`` and Phase 29+ will populate ``total_cost_usd`` once
    a per-model price table lands.
    """

    session_key: str
    model: str = "—"
    permission_mode: str = "auto"
    total_tokens: int = 0
    ctx_max: int = DEFAULT_CTX_MAX
    total_cost_usd: float | None = None
    registry: Any = None  # SlashRegistry; Any to avoid import cycle
    print_line: Any = print
    pending_attachments: list[Any] = field(default_factory=list)
    current_turn_task: Any = None  # asyncio.Task | None


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
    """Stream one turn to the renderer chosen for the current TTY."""
    from lite_horse.api import (
        StreamDelta,
        StreamDone,
        StreamToolCall,
        StreamToolOutput,
        run_turn_streaming,
    )
    from lite_horse.cli.repl.renderer import make_renderer

    tty = detect()
    renderer = make_renderer(use_color=tty.use_color, stdout_tty=tty.stdout_tty)
    asm = StreamAssembler()
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
                # Tool announce — print to stderr to avoid contaminating the
                # chat stream. Phase 28 turns this into a rich panel.
                print(f"[tool: {ev.name}]", file=sys.stderr, flush=True)
            elif isinstance(ev, StreamToolOutput):
                print(f"[tool {ev.name} → {ev.output[:80]}]",
                      file=sys.stderr, flush=True)
            elif isinstance(ev, StreamDone):
                final = asm.finalize(ev.result.final_output)
                renderer.update(final)
                if ev.result.total_tokens is not None:
                    state.total_tokens += ev.result.total_tokens
    finally:
        renderer.stop()


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
    session = build_prompt_session(state.registry, bottom_toolbar=build_bottom_toolbar(state))
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
        if await _await_with_two_press_ctrl_c(state, line):
            print("[exit]", file=sys.stderr)
            return 0


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
