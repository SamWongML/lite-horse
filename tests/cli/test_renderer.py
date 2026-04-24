"""Renderer unit tests — covers the plain fallback path end-to-end.

The Live renderer is exercised indirectly via the one-shot dispatch test
(NO_COLOR forces the plain path); the live one is left to the pexpect
smoke + manual verification because asserting against rich.live internals
is brittle.
"""
from __future__ import annotations

import pytest

from lite_horse.cli.repl.renderer import (
    LiveStreamRenderer,
    PlainStreamRenderer,
    make_renderer,
)


def test_plain_renderer_prints_only_new_suffix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = PlainStreamRenderer()
    r.start()
    r.update("Hello")
    r.update("Hello, world")
    r.stop()
    out = capsys.readouterr().out
    assert out.startswith("Hello, world")  # never duplicates "Hello"
    assert out.endswith("\n")  # stop() appends newline


def test_plain_renderer_drift_starts_fresh_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = PlainStreamRenderer()
    r.start()
    r.update("partial")
    r.update("totally different")
    r.stop()
    out = capsys.readouterr().out
    assert "totally different" in out
    # The drift handler injects a leading newline before the swap.
    assert "\ntotally different" in out


def test_plain_renderer_stop_no_double_newline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = PlainStreamRenderer()
    r.start()
    r.update("ends with newline\n")
    r.stop()
    out = capsys.readouterr().out
    assert out == "ends with newline\n"


def test_make_renderer_plain_when_no_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = make_renderer(use_color=False, stdout_tty=True)
    assert isinstance(r, PlainStreamRenderer)


def test_make_renderer_plain_when_not_tty() -> None:
    r = make_renderer(use_color=True, stdout_tty=False)
    assert isinstance(r, PlainStreamRenderer)


def test_make_renderer_live_when_color_and_tty() -> None:
    r = make_renderer(use_color=True, stdout_tty=True)
    assert isinstance(r, LiveStreamRenderer)
