"""`litehorse` CLI entrypoint.

Exposes three subcommands:

- `chat`    — interactive REPL (Phase 8, implemented)
- `gateway` — Telegram gateway runner (Phase 9, implemented)
- `cron`    — APScheduler worker (Phase 10, stub)
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import click
from agents import Runner
from rich.console import Console

from lite_horse import __version__
from lite_horse.agent.factory import build_agent
from lite_horse.config import Config, load_config
from lite_horse.constants import litehorse_home
from lite_horse.gateway.runner import run_gateway
from lite_horse.sessions.db import SessionDB
from lite_horse.sessions.sdk_session import SDKSession
from lite_horse.sessions.search_tool import bind_db
from lite_horse.skills.source import sync_bundled_skills

_DB: SessionDB | None = None
_CONSOLE = Console()

_EXIT_COMMANDS = {"/exit", "/quit", ":q"}


def _ensure_state_dirs() -> None:
    """Create state subdirectories and copy bundled skills on first run."""
    home = litehorse_home()
    for sub in ("memories", "skills", "sessions"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    sync_bundled_skills()


def _startup() -> SessionDB:
    """Prepare state dirs and bind the session DB used by tool singletons."""
    global _DB  # noqa: PLW0603 — single-process singleton, intentional
    _ensure_state_dirs()
    if _DB is None:
        _DB = SessionDB()
    bind_db(_DB)
    return _DB


async def _default_input(prompt: str) -> str:
    """Read a line from the user, off-thread so asyncio stays responsive."""
    return await asyncio.to_thread(_CONSOLE.input, prompt)


async def _repl_loop(
    *,
    session: SDKSession,
    agent: object,
    max_turns: int,
    console: Console,
    input_fn: Callable[[str], Awaitable[str]],
) -> None:
    """Drive the REPL until the user exits or stdin closes."""
    while True:
        try:
            user_in = await input_fn("[bold cyan]you[/]: ")
        except (EOFError, KeyboardInterrupt):
            break
        stripped = user_in.strip()
        if stripped in _EXIT_COMMANDS:
            break
        if not stripped:
            continue
        try:
            result = await Runner.run(
                agent,  # type: ignore[arg-type]
                user_in,
                session=session,  # type: ignore[arg-type]
                max_turns=max_turns,
            )
        except Exception as exc:
            console.print(f"[red]error:[/] {exc}")
            continue
        console.print(f"[bold green]horse[/]: {result.final_output}\n")


@click.group()
@click.version_option(__version__, prog_name="litehorse")
def main() -> None:
    """lite-horse: OpenAI-only personal assistant."""
    _startup()


@main.command()
@click.option(
    "--session-id",
    "session_id",
    default=None,
    help="Resume an existing session by id.",
)
def chat(session_id: str | None) -> None:
    """Interactive chat REPL with full session persistence."""
    db = _startup()
    cfg: Config = load_config()
    sid = session_id or f"cli-{uuid.uuid4().hex[:12]}"
    session = SDKSession(sid, db, source="cli", model=cfg.model)
    agent = build_agent(config=cfg)

    _CONSOLE.print(
        f"[dim]session: {sid}[/dim]\n[dim]type /exit, /quit, or :q to quit[/dim]\n"
    )

    try:
        asyncio.run(
            _repl_loop(
                session=session,
                agent=agent,
                max_turns=cfg.agent.max_turns,
                console=_CONSOLE,
                input_fn=_default_input,
            )
        )
    finally:
        db.end_session(sid)


@main.command()
def gateway() -> None:
    """Run the Telegram gateway."""
    _startup()
    asyncio.run(run_gateway())


@main.command()
def cron() -> None:
    """Run the cron scheduler (not yet implemented)."""
    raise click.ClickException("cron is not implemented yet (Phase 10).")


if __name__ == "__main__":
    main()
