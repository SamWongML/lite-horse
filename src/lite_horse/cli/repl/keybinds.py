"""prompt_toolkit key bindings for the REPL prompt.

Two-press Ctrl-C cancellation lives in :mod:`lite_horse.cli.repl.loop` because
it needs the asyncio task handle, not the prompt buffer. Here we only bind
the keys that act on the editor itself.
"""
from __future__ import annotations

from typing import Any


def make_prompt_keybindings() -> Any:
    """Return ``KeyBindings`` for the prompt: Esc-Enter, Ctrl-D, Ctrl-L.

    Heavy import deferred so the module is cheap to load on the ``--help``
    fast-path.
    """
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("c-d")
    def _ctrl_d(event: Any) -> None:
        if not event.current_buffer.text:
            event.app.exit(exception=EOFError())

    @kb.add("c-l")
    def _ctrl_l(event: Any) -> None:
        event.app.renderer.clear()

    return kb
