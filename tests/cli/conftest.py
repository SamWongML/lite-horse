"""Shared fixtures for CLI tests."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def litehorse_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate `~/.litehorse` state per test via `LITEHORSE_HOME` override."""
    home = tmp_path / "state"
    home.mkdir()
    monkeypatch.setenv("LITEHORSE_HOME", str(home))
    # Scrub any inherited LITEHORSE_* env so settings tests start clean.
    for key in list(os.environ):
        if key.startswith("LITEHORSE_") and key != "LITEHORSE_HOME":
            monkeypatch.delenv(key, raising=False)
    yield home
