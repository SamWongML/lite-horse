"""Signal handling for scripted (non-REPL) CLI commands.

Scripted mode policy:
  SIGINT  → Python's default (raise KeyboardInterrupt); caller translates to
            exit 130 via `scripted_signal_guard`.
  SIGTERM → clean exit with code 143.

The REPL installs its own Ctrl-C / Ctrl-D bindings via prompt_toolkit
KeyBindings; it must NOT call this module.
"""
from __future__ import annotations

import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from lite_horse.cli.exit_codes import ExitCode


def _sigterm_handler(_signum: int, _frame: object) -> None:
    sys.exit(int(ExitCode.SIGTERM))


def install_scripted_handlers() -> None:
    """Install SIGTERM → exit 143. Idempotent; safe to call twice."""
    signal.signal(signal.SIGTERM, _sigterm_handler)


@contextmanager
def scripted_signal_guard() -> Iterator[None]:
    """Translate KeyboardInterrupt into exit 130 for scripted commands."""
    install_scripted_handlers()
    try:
        yield
    except KeyboardInterrupt:
        sys.exit(int(ExitCode.SIGINT))
