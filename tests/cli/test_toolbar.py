from __future__ import annotations

from lite_horse.cli.repl.toolbar import build_bottom_toolbar, format_toolbar


def test_format_toolbar_includes_all_fields() -> None:
    line = format_toolbar(
        model="gpt-5.4",
        session_key="agent:cli:repl:local:abcdef",
        total_tokens=1234,
        ctx_max=200_000,
        cost_usd=0.0123,
        permission_mode="auto",
    )
    assert "gpt-5.4" in line
    assert "session:agent:cl" in line  # first 8 chars of key
    assert "ctx:1234/200000" in line
    assert "0.6%" in line  # 1234/200_000 = 0.617%
    assert "$0.0123" in line
    assert "[auto]" in line


def test_format_toolbar_handles_empty_state() -> None:
    line = format_toolbar(
        model="",
        session_key="",
        total_tokens=0,
        ctx_max=0,
        cost_usd=None,
        permission_mode="ro",
    )
    assert "—" in line  # model fallback
    assert "session:—" in line
    assert "$—" in line
    assert "[ro]" in line


def test_format_toolbar_zero_ctx_no_div_zero() -> None:
    line = format_toolbar(
        model="m",
        session_key="k",
        total_tokens=10,
        ctx_max=0,
        cost_usd=0.0,
        permission_mode="ask",
    )
    assert "0.0%" in line


def test_build_bottom_toolbar_reads_state_dynamically() -> None:
    class State:
        model = "m1"
        session_key = "key12345xx"
        total_tokens = 0
        ctx_max = 1000
        total_cost_usd = None
        permission_mode = "auto"

    state = State()
    cb = build_bottom_toolbar(state)
    first = str(cb())
    assert "m1" in first and "ctx:0/1000" in first

    state.total_tokens = 500
    state.model = "m2"
    second = str(cb())
    assert "m2" in second and "ctx:500/1000" in second
    assert "50.0%" in second


def test_build_bottom_toolbar_escapes_html_special_chars() -> None:
    class State:
        model = "<script>"
        session_key = "k"
        total_tokens = 0
        ctx_max = 100
        total_cost_usd = 0.0
        permission_mode = "auto"

    rendered = str(build_bottom_toolbar(State())())
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered or "script" in rendered
