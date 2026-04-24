"""Pexpect smoke test — boots the real ``litehorse`` and exits cleanly.

Skipped when pexpect cannot allocate a pty (e.g. CI containers without
a tty). Marked ``slow`` because it spawns a subprocess; run with
``pytest -m slow`` to opt in.

Asserts only the prompt loop, not any model interaction. We:
  1. spawn ``uv run litehorse``
  2. wait for the prompt banner
  3. send ``/help`` followed by Esc-Enter (REPL is multiline; plain
     Enter inserts a newline rather than submitting)
  4. expect to see ``/exit`` listed
  5. send Ctrl-D
  6. assert exit 0
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

pexpect = pytest.importorskip("pexpect")

try:
    import pty  # noqa: F401  # presence check only — see _have_pty
    _HAVE_PTY = True
except ImportError:
    _HAVE_PTY = False


def _have_pty() -> bool:
    return _HAVE_PTY


@pytest.mark.slow
@pytest.mark.skipif(not _have_pty(), reason="no pty available")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_litehorse_boots_help_and_exits(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["LITEHORSE_HOME"] = str(tmp_path)
    # The REPL doesn't talk to OpenAI on /help + Ctrl-D, but a value
    # still has to be present in case any lazy init touches the client.
    env.setdefault("OPENAI_API_KEY", "sk-pexpect-fake")

    child = pexpect.spawn(  # type: ignore[attr-defined]
        "uv", ["run", "litehorse"], env=env, timeout=20, encoding="utf-8"
    )
    try:
        child.expect_exact(">>>")
        # Esc-Enter submits in multiline mode; plain \n would only
        # insert a newline into the buffer.
        child.send("/help\x1b\r")
        child.expect_exact("/exit")
        child.sendcontrol("d")
        child.expect(pexpect.EOF)  # type: ignore[attr-defined]
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.terminate(force=True)
