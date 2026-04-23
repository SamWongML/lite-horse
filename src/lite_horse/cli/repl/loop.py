"""REPL main loop — full implementation arrives in Phase 27.

Phase 26 ships a stub so the `litehorse` entry point resolves and `litehorse
--help` works. Invoking the REPL prints a hint and exits cleanly.
"""
from __future__ import annotations

import sys

STUB_MESSAGE = (
    "[litehorse] interactive REPL is not implemented yet — wired in Phase 27.\n"
    "In the meantime, use `litehorse-debug` for a minimal local chat, or\n"
    "import `lite_horse.api.run_turn` from Python."
)


def run_stub(prompt: str | None = None) -> int:
    """Print the stub message and return 0.

    `prompt` is accepted so the stub accepts the same positional-arg shape
    the real REPL will ship with; it is ignored here.
    """
    del prompt  # unused in the stub
    print(STUB_MESSAGE, file=sys.stderr)
    return 0
