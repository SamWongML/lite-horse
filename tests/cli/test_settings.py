from pathlib import Path

import pytest

from lite_horse.cli import _settings


def test_defaults_when_env_empty(litehorse_home: Path) -> None:
    s = _settings.load()
    assert s.debug is False
    assert s.json_output is False
    assert s.structured_logs is False


def test_env_override(litehorse_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LITEHORSE_DEBUG", "true")
    monkeypatch.setenv("LITEHORSE_JSON_OUTPUT", "1")
    s = _settings.load()
    assert s.debug is True
    assert s.json_output is True


def test_state_dir_tracks_litehorse_home(litehorse_home: Path) -> None:
    assert _settings.state_dir() == litehorse_home


def test_env_file_is_read(litehorse_home: Path) -> None:
    (litehorse_home / ".env").write_text("LITEHORSE_STRUCTURED_LOGS=true\n")
    s = _settings.load()
    assert s.structured_logs is True
