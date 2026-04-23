"""Fast-path contract for `litehorse --help`.

`--help` must return quickly and without loading openai, prompt_toolkit,
or rich — see docs/plans/v0.3-cli-entrypoint.md "--help fast-path". We
run in a subprocess so this test does not see modules pulled in by
sibling test files.
"""
from __future__ import annotations

import subprocess
import sys

_FAST_PATH_FORBIDDEN = ("openai", "prompt_toolkit", "rich")


def _run_help_with_importtime() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "importtime",
            "-c",
            "from lite_horse.cli.app import main; main()",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_help_exits_zero() -> None:
    result = _run_help_with_importtime()
    assert result.returncode == 0
    assert "Usage" in result.stdout


def test_help_does_not_import_heavy_modules() -> None:
    result = _run_help_with_importtime()
    # `-X importtime` writes one line per module to stderr, e.g.:
    #   import time:        37 |         37 |   _locale
    # We split on ` | ` and take the module name (last column).
    loaded: set[str] = set()
    for line in result.stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line.split(" | ")
        if not parts:
            continue
        name = parts[-1].strip()
        # Only care about top-level package names.
        loaded.add(name.split(".", 1)[0])

    offenders = [mod for mod in _FAST_PATH_FORBIDDEN if mod in loaded]
    assert not offenders, (
        f"--help path loaded forbidden modules: {offenders}. "
        f"Move their imports inside command bodies."
    )
