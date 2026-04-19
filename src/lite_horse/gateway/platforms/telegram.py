"""Telegram adapter — single bot, allowlist of Telegram user IDs.

The adapter is responsible for (a) rejecting anything from outside the
allowlist, and (b) translating incoming updates into a platform-neutral event
dict that the gateway runner can dispatch. It holds no agent/session state of
its own.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

log = logging.getLogger(__name__)

MessageEvent = dict[str, Any]
OnMessage = Callable[[MessageEvent], Awaitable[None]]


class TelegramAdapter:
    """Owns the python-telegram-bot ``Application`` and routes text updates."""

    def __init__(
        self,
        *,
        token: str,
        allowed_user_ids: set[int],
        on_message: OnMessage,
    ) -> None:
        self.token = token
        self.allowed = allowed_user_ids
        self.on_message = on_message
        self.app: Application | None = None  # type: ignore[type-arg]

    async def start(self) -> None:
        self.app = Application.builder().token(self.token).build()
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self.app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        await self.app.initialize()
        await self.app.start()
        if self.app.updater is not None:
            await self.app.updater.start_polling()

    async def stop(self) -> None:
        if self.app is None:
            return
        if self.app.updater is not None:
            await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def _on_text(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if user is None or msg is None or chat is None:
            return
        if user.id not in self.allowed:
            log.warning("rejecting message from non-allowed user %s", user.id)
            return

        async def send_reply(text: str) -> None:
            await msg.reply_text(text)

        await self.on_message(
            {
                "platform": "telegram",
                "chat_type": chat.type,
                "chat_id": chat.id,
                "user_id": user.id,
                "text": msg.text or "",
                "is_command": False,
                "send_reply": send_reply,
            }
        )

    async def _on_command(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        # Treat /cmd like text; the runner decides whether to special-case it.
        await self._on_text(update, ctx)
