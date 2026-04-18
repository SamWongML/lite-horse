"""`hermeslite` CLI entrypoint.

Exposes three subcommands as stubs during Phase 0; each will be wired up in
later phases:

- `chat`    — interactive REPL (Phase 8)
- `gateway` — Telegram gateway runner (Phase 9)
- `cron`    — APScheduler worker (Phase 10)
"""
from __future__ import annotations

import click

from hermes_lite import __version__
from hermes_lite.constants import hermeslite_home
from hermes_lite.skills.source import sync_bundled_skills


def _ensure_state_dirs() -> None:
    """Create state subdirectories and copy bundled skills on first run."""
    home = hermeslite_home()
    for sub in ("memories", "skills", "sessions"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    sync_bundled_skills()


@click.group()
@click.version_option(__version__, prog_name="hermeslite")
def main() -> None:
    """hermes-lite: OpenAI-only personal assistant."""
    _ensure_state_dirs()


@main.command()
def chat() -> None:
    """Start an interactive chat session (not yet implemented)."""
    raise click.ClickException("chat is not implemented yet (Phase 8).")


@main.command()
def gateway() -> None:
    """Run the Telegram gateway (not yet implemented)."""
    raise click.ClickException("gateway is not implemented yet (Phase 9).")


@main.command()
def cron() -> None:
    """Run the cron scheduler (not yet implemented)."""
    raise click.ClickException("cron is not implemented yet (Phase 10).")


if __name__ == "__main__":
    main()
