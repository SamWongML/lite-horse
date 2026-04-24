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

import sys
from dataclasses import dataclass, field
from typing import Any

from lite_horse.cli._tty import detect
from lite_horse.cli.repl.slash import SlashOutcome, dispatch, parse_slash
from lite_horse.cli.repl.slash_handlers.session import build_default_registry
from lite_horse.cli.repl.stream_buf import StreamAssembler
from lite_horse.core.session_key import build_session_key


@dataclass
class ReplState:
    """Mutable state threaded through slash handlers and the loop body.

    Subsequent commits add ``model``, ``permission_mode``, ``tokens``,
    ``cost``, ``allowed_tools``/``denied_tools``. For commit 2 we keep just
    enough to wire /help.
    """

    session_key: str
    registry: Any = None  # SlashRegistry; Any to avoid import cycle
    print_line: Any = print
    pending_attachments: list[Any] = field(default_factory=list)


def _default_session_key() -> str:
    return build_session_key(platform="cli", chat_type="repl", chat_id="local")


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
    finally:
        renderer.stop()


async def _interactive_loop(state: ReplState) -> int:
    """Drive a prompt_toolkit PromptSession until /exit or Ctrl-D."""
    from prompt_toolkit.patch_stdout import patch_stdout

    from lite_horse.cli.repl.session import build_prompt_session, submit_keybindings

    assert state.registry is not None
    session = build_prompt_session(state.registry)
    kb = submit_keybindings()
    print("litehorse — type /help for commands, Esc-Enter to submit, Ctrl-D to exit",
          file=sys.stderr)
    while True:
        try:
            with patch_stdout():
                line = await session.prompt_async(">>> ", key_bindings=kb)
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
        try:
            await _run_one_turn(state, line)
        except Exception as exc:
            print(f"[error: {exc}]", file=sys.stderr)


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
