"""Tests for ``build_session_key`` (moved from gateway → core in Phase 15)."""
from __future__ import annotations

from lite_horse.core.session_key import build_session_key


def test_private_chat_key_shape() -> None:
    key = build_session_key(
        platform="web", chat_type="private", chat_id=42
    )
    assert key == "agent:main:web:private:42"


def test_group_chat_key_shape() -> None:
    key = build_session_key(
        platform="web", chat_type="group", chat_id=-100123
    )
    assert key == "agent:main:web:group:-100123"


def test_thread_id_appends_when_provided() -> None:
    key = build_session_key(
        platform="web", chat_type="supergroup", chat_id=7, thread_id=99
    )
    assert key == "agent:main:web:supergroup:7:99"


def test_thread_id_omitted_when_none() -> None:
    key = build_session_key(
        platform="web", chat_type="private", chat_id="abc", thread_id=None
    )
    assert key == "agent:main:web:private:abc"


def test_thread_id_zero_is_kept() -> None:
    key = build_session_key(
        platform="web", chat_type="group", chat_id=1, thread_id=0
    )
    assert key == "agent:main:web:group:1:0"
