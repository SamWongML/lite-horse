"""Adapter exposing :class:`SessionDB` as an OpenAI Agents SDK ``Session``."""
from __future__ import annotations

from typing import Any

from lite_horse.sessions.db import SessionDB


class SDKSession:
    """Implements the SDK ``Session`` protocol on top of :class:`SessionDB`."""

    def __init__(
        self,
        session_id: str,
        db: SessionDB,
        *,
        source: str = "cli",
        user_id: str | None = None,
        model: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.db = db
        self.db.create_session(
            session_id=session_id, source=source, user_id=user_id, model=model
        )

    async def get_items(self, limit: int | None = None) -> list[dict[str, Any]]:
        return self.db.get_messages(self.session_id, limit=limit)

    async def add_items(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            raw_tool_calls = item.get("tool_calls")
            tool_calls: list[dict[str, Any]] | None
            if isinstance(raw_tool_calls, list):
                tool_calls = [tc for tc in raw_tool_calls if isinstance(tc, dict)]
            else:
                tool_calls = None
            content_val = item.get("content")
            content_str: str | None
            if content_val is None:
                content_str = None
            elif isinstance(content_val, str):
                content_str = content_val
            else:
                # The SDK may pass structured content (list/dict). We persist a
                # string representation so FTS5 can still tokenize it.
                content_str = str(content_val)
            self.db.append_message(
                session_id=self.session_id,
                role=str(item.get("role", "assistant")),
                content=content_str,
                tool_call_id=item.get("tool_call_id"),
                tool_calls=tool_calls,
                tool_name=item.get("name"),
            )

    async def pop_item(self) -> dict[str, Any] | None:
        return self.db.pop_last_message(self.session_id)

    async def clear_session(self) -> None:
        self.db.clear_session(self.session_id)
