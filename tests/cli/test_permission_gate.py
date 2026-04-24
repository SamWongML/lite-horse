"""Tests for ``lite_horse.core.permission`` and factory tool filtering."""
from __future__ import annotations

from lite_horse.core.permission import (
    WRITE_TOOL_NAMES,
    PermissionPolicy,
    filter_tools,
    normalize_mode,
)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_normalize_mode_aliases() -> None:
    assert normalize_mode("auto") == "auto"
    assert normalize_mode("ask") == "ask"
    assert normalize_mode("ro") == "ro"
    assert normalize_mode("RO") == "ro"
    assert normalize_mode("read-only") == "ro"
    assert normalize_mode("readonly") == "ro"
    assert normalize_mode("bogus") is None


def test_is_tool_allowed_auto_offers_everything() -> None:
    p = PermissionPolicy(mode="auto")
    for name in ("memory", "skill_manage", "cron_manage", "skill_view"):
        assert p.is_tool_allowed(name)


def test_is_tool_allowed_ro_filters_writes() -> None:
    p = PermissionPolicy(mode="ro")
    for name in WRITE_TOOL_NAMES:
        assert not p.is_tool_allowed(name)
    assert p.is_tool_allowed("skill_view")
    assert p.is_tool_allowed("session_search")


def test_is_tool_allowed_ask_offers_everything() -> None:
    # ``ask`` leaves tools on the menu — the runtime prompt (not build-time
    # filtering) is what gates in ``ask`` mode.
    p = PermissionPolicy(mode="ask")
    for name in WRITE_TOOL_NAMES:
        assert p.is_tool_allowed(name)


def test_filter_tools_removes_write_tools_under_ro() -> None:
    tools = [_FakeTool(n) for n in ("memory", "skill_view", "cron_manage", "session_search")]
    kept = filter_tools(tools, PermissionPolicy(mode="ro"))
    kept_names = {t.name for t in kept}
    assert kept_names == {"skill_view", "session_search"}


def test_filter_tools_noop_under_auto() -> None:
    tools = [_FakeTool(n) for n in ("memory", "skill_view", "cron_manage")]
    kept = filter_tools(tools, PermissionPolicy(mode="auto"))
    assert [t.name for t in kept] == ["memory", "skill_view", "cron_manage"]
