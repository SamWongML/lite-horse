"""Invariant: importing `lite_horse.api` must not pull in the CLI or evolve.

The webapp-facing package (`lite_horse.api`) is the embedded surface. It
must stay lean; CLI-only and evolve-only modules should never be loaded
just because a webapp imports the API.

Tested via subprocess so sibling pytest modules don't pollute sys.modules.
"""
from __future__ import annotations

import subprocess
import sys


def test_api_does_not_transitively_load_cli() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, lite_horse.api; "
            "banned=[m for m in sys.modules "
            "if m == 'lite_horse.cli' or m.startswith('lite_horse.cli.')]; "
            "assert not banned, banned; print('ok')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_api_does_not_transitively_load_evolve() -> None:
    # Already covered in tests/test_evolve_e2e.py; duplicated here as a
    # neighbor check so CLI developers get a fast local signal.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, lite_horse.api; "
            "assert 'lite_horse.evolve' not in sys.modules; print('ok')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
