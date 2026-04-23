import pytest

from lite_horse.cli import _tty


def test_no_color_env_wins_over_stdout_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    info = _tty.detect()
    assert info.no_color is True
    assert info.use_color is False


def test_force_color_overrides_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert _tty.detect().use_color is True


def test_dumb_terminal_is_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    info = _tty.detect()
    assert info.no_color is True
