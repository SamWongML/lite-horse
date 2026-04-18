"""Shared pytest fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture()
def hermeslite_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point HERMESLITE_HOME at an isolated temp directory."""
    monkeypatch.setenv("HERMESLITE_HOME", str(tmp_path))
    yield tmp_path
