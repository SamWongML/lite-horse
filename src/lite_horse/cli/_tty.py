"""TTY / color detection for the CLI.

Kept dependency-free so it can be evaluated on the `--help` fast-path
without pulling rich or prompt_toolkit.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class TtyInfo:
    stdin_tty: bool
    stdout_tty: bool
    no_color: bool
    force_color: bool

    @property
    def use_color(self) -> bool:
        if self.force_color:
            return True
        if self.no_color:
            return False
        return self.stdout_tty


def detect() -> TtyInfo:
    stdin_tty = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
    stdout_tty = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
    no_color = "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb"
    force_color = "FORCE_COLOR" in os.environ
    return TtyInfo(
        stdin_tty=stdin_tty,
        stdout_tty=stdout_tty,
        no_color=no_color,
        force_color=force_color,
    )
