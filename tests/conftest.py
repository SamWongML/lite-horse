"""Shared pytest fixtures."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from lite_horse import cli
from lite_horse.sessions import search_tool as _search_tool


@pytest.fixture()
def litehorse_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point LITEHORSE_HOME at an isolated temp directory.

    Also resets the module-level ``cli._DB`` / ``search_tool._DB`` singletons so
    each test sees a fresh state dir rather than a leftover from a prior run.
    """
    monkeypatch.setenv("LITEHORSE_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_DB", None, raising=False)
    monkeypatch.setattr(_search_tool, "_DB", None, raising=False)
    yield tmp_path
