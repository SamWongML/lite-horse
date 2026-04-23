"""`litehorse completion install <shell>` — filled in commit 6."""
from __future__ import annotations

import typer

app = typer.Typer(help="Install shell completion for litehorse.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Force typer to emit a Group so `litehorse completion install` routes."""


@app.command()
def install(shell: str) -> None:  # pragma: no cover - replaced in commit 6
    del shell
    raise SystemExit("completion install arrives in a follow-up commit")
