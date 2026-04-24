from __future__ import annotations

from lite_horse.cli.repl.stream_buf import StreamAssembler


def test_initial_state() -> None:
    a = StreamAssembler()
    assert a.text == ""
    assert a.started is False
    assert a.box_opened is False


def test_feed_concatenates_and_marks_started() -> None:
    a = StreamAssembler()
    a.feed("Hello, ")
    a.feed("world!")
    assert a.text == "Hello, world!"
    assert a.started is True


def test_feed_empty_delta_is_noop() -> None:
    a = StreamAssembler()
    a.feed("")
    assert a.text == ""
    assert a.started is False


def test_box_opened_flag() -> None:
    a = StreamAssembler()
    a.feed("hi")
    assert a.box_opened is False
    a.mark_box_opened()
    assert a.box_opened is True


def test_finalize_swaps_when_drift() -> None:
    a = StreamAssembler()
    a.feed("partial")
    out = a.finalize("partial complete")
    assert out == "partial complete"
    assert a.text == "partial complete"


def test_finalize_no_drift_keeps_text() -> None:
    a = StreamAssembler()
    a.feed("done")
    out = a.finalize("done")
    assert out == "done"
    assert a.text == "done"


def test_finalize_none_keeps_buffer() -> None:
    a = StreamAssembler()
    a.feed("hi")
    out = a.finalize(None)
    assert out == "hi"


def test_reset_clears_state() -> None:
    a = StreamAssembler()
    a.feed("x", item_id="msg_1")
    a.mark_box_opened()
    a.reset()
    assert a.text == ""
    assert a.started is False
    assert a.box_opened is False
