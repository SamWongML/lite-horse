"""prompt_toolkit ``PromptSession`` builder for the REPL.

Heavy imports (``prompt_toolkit``, ``rich``) live inside the function body so
``litehorse --help`` stays under the 200 ms fast-path budget.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lite_horse.cli._settings import state_dir
from lite_horse.cli.repl.slash import SlashRegistry


def _history_path() -> Path:
    return state_dir() / "history"


def build_prompt_session(registry: SlashRegistry) -> Any:
    """Construct a ``PromptSession`` wired with our slash completions.

    Returns ``Any`` so this module's signature stays prompt_toolkit-free
    on import (callers that want concrete types import locally).
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, WordCompleter
    from prompt_toolkit.history import FileHistory

    inner = WordCompleter(
        ["/" + w for w in registry.names()],
        ignore_case=True,
        sentence=True,
    )

    class _SlashCompleter(Completer):  # type: ignore[misc]
        """Slash-only completer — fires only when buffer starts with ``/``."""

        def get_completions(self, document: Any, complete_event: Any) -> Any:
            if not document.text_before_cursor.lstrip().startswith("/"):
                return iter(())
            return inner.get_completions(document, complete_event)

    history_path = _history_path()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.touch(exist_ok=True)

    return PromptSession(
        history=FileHistory(str(history_path)),
        multiline=True,
        enable_open_in_editor=True,
        complete_while_typing=True,
        completer=_SlashCompleter(),
    )


def submit_keybindings() -> Any:
    """KeyBindings: bare Enter inserts newline; Meta-Enter / Esc-Enter submits.

    prompt_toolkit's ``multiline=True`` defaults already do this, but we install
    explicit bindings so the behaviour is documented and stable across versions.
    """
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("escape", "enter")  # type: ignore[untyped-decorator]
    def _submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("c-d")  # type: ignore[untyped-decorator]
    def _ctrl_d(event: Any) -> None:
        if not event.current_buffer.text:
            event.app.exit(exception=EOFError())

    return kb
