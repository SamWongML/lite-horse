"""Session-key construction for gateway-delivered messages.

The canonical form is ``agent:main:{platform}:{chat_type}:{chat_id}`` with an
optional ``:{thread_id}`` suffix for forum-style threads.
"""
from __future__ import annotations


def build_session_key(
    *,
    platform: str,
    chat_type: str,
    chat_id: int | str,
    thread_id: int | str | None = None,
) -> str:
    """Return the session key used by the gateway to scope conversations."""
    base = f"agent:main:{platform}:{chat_type}:{chat_id}"
    return f"{base}:{thread_id}" if thread_id is not None else base
