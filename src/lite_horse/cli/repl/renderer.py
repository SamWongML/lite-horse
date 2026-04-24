"""Streaming markdown renderer for the REPL.

Two concrete implementations behind a tiny protocol:

- :class:`LiveStreamRenderer` — wraps ``rich.live.Live(Markdown(buf))``,
  ``refresh_per_second=12``. Used when stdout is a TTY and color is on.
- :class:`PlainStreamRenderer` — plain ``print`` of each delta. Used when
  stdout is not a TTY, ``NO_COLOR`` is set, or ``TERM=dumb``.

The renderer is fed by the REPL loop with the cumulative text from
:class:`StreamAssembler` (not the raw delta) so re-renders are idempotent.
"""
from __future__ import annotations

from typing import Any, Protocol


class StreamRenderer(Protocol):
    def start(self) -> None: ...
    def update(self, text: str) -> None: ...
    def stop(self) -> None: ...


class LiveStreamRenderer:
    """Rich live-Markdown renderer.

    Defers ``rich`` imports until ``start()`` so module import stays cheap.
    """

    def __init__(self, *, console: Any | None = None) -> None:
        self._console = console
        self._live: Any | None = None

    def start(self) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.markdown import Markdown

        if self._console is None:
            self._console = Console()
        self._live = Live(
            Markdown(""),
            console=self._console,
            refresh_per_second=12,
            vertical_overflow="visible",
        )
        self._live.__enter__()

    def update(self, text: str) -> None:
        from rich.markdown import Markdown

        if self._live is None:
            return
        self._live.update(Markdown(text))

    def stop(self) -> None:
        if self._live is not None:
            self._live.__exit__(None, None, None)
            self._live = None


class PlainStreamRenderer:
    """No-frills renderer: prints each delta to stdout, flushes."""

    def __init__(self) -> None:
        self._last = ""

    def start(self) -> None:
        self._last = ""

    def update(self, text: str) -> None:
        # We get cumulative text; print only the new suffix.
        if text.startswith(self._last):
            new = text[len(self._last):]
        else:
            # Final text drifted from streamed buffer — print the whole thing
            # on a fresh line.
            new = "\n" + text
        if new:
            print(new, end="", flush=True)
            self._last = text

    def stop(self) -> None:
        if self._last and not self._last.endswith("\n"):
            print()
        self._last = ""


def make_renderer(*, use_color: bool, stdout_tty: bool) -> StreamRenderer:
    """Pick the renderer that fits the current terminal."""
    if use_color and stdout_tty:
        return LiveStreamRenderer()
    return PlainStreamRenderer()
