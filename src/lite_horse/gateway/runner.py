"""GatewayRunner — owns adapters, dispatches messages under the two-level guard.

One process per runner. Held state:
  * one :class:`SessionDB` (shared via ``bind_db`` with the tool singletons),
  * one :class:`GuardRegistry` mapping session keys → per-key locks/queues,
  * one :class:`TelegramAdapter` pulling updates from python-telegram-bot.

Shutdown is signal-driven: SIGINT / SIGTERM flip a stop ``Event`` and the
runner drains the adapter and removes ``gateway.pid`` before returning.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from agents import Runner

from lite_horse.agent.factory import build_agent
from lite_horse.config import Config, load_config
from lite_horse.constants import litehorse_home
from lite_horse.gateway.guard import GuardRegistry
from lite_horse.gateway.platforms.telegram import TelegramAdapter
from lite_horse.gateway.session_key import build_session_key
from lite_horse.sessions.db import SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def make_handler(
    *, db: SessionDB, guards: GuardRegistry, cfg: Config
) -> Handler:
    """Build the dispatcher closure used by the platform adapter.

    Exposed so tests can drive the queue/interrupt behavior without standing up
    a real Telegram application.
    """

    async def handle(event: dict[str, Any]) -> None:
        sk = build_session_key(
            platform=event["platform"],
            chat_type=event["chat_type"],
            chat_id=event["chat_id"],
        )
        guard = guards.get(sk)
        # Level 1: a run is already in flight for this session → queue + wake.
        if guard.lock.locked():
            guard.pending.append(event["text"])
            guard.interrupt.set()
            return
        async with guard.lock:
            text = event["text"]
            # Level 2: drain any pending messages so ordering is preserved.
            if guard.pending:
                text = "\n\n".join([*guard.pending, text])
                guard.pending.clear()
                guard.interrupt.clear()
            session = SDKSession(sk, db, source="telegram")
            agent = build_agent(config=cfg)
            try:
                result = await Runner.run(agent, text, session=session)  # type: ignore[arg-type]
                await event["send_reply"](result.final_output)
            except Exception as exc:
                log.exception("agent run failed")
                await event["send_reply"](f"⚠ error: {exc}")

    return handle


async def run_gateway() -> None:
    """Start the Telegram gateway and run until SIGINT / SIGTERM."""
    cfg = load_config()
    if not cfg.gateway.telegram.enabled:
        raise SystemExit("Telegram is disabled in config.yaml")
    try:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
    except KeyError as exc:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set") from exc
    allowed = {int(x) for x in cfg.gateway.telegram.allowed_user_ids}
    if not allowed:
        raise SystemExit(
            "gateway.telegram.allowed_user_ids is empty — refusing to start"
        )

    db = SessionDB()
    bind_db(db)
    guards = GuardRegistry()

    home = litehorse_home()
    home.mkdir(parents=True, exist_ok=True)
    pid_file = home / "gateway.pid"
    pid_file.write_text(str(os.getpid()))

    handle = make_handler(db=db, guards=guards, cfg=cfg)

    adapter = TelegramAdapter(
        token=token, allowed_user_ids=allowed, on_message=handle
    )
    await adapter.start()
    log.info("gateway up; allowed users: %s", sorted(allowed))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await stop.wait()
    finally:
        await adapter.stop()
        if pid_file.exists():
            pid_file.unlink()
