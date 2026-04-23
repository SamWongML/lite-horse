"""Documented exit codes for the `litehorse` CLI."""
from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    GENERIC = 1
    USAGE = 2
    CONFIG = 3
    AUTH = 4
    NOT_FOUND = 5
    CONFLICT = 6
    IO = 7
    SIGINT = 130
    SIGTERM = 143
