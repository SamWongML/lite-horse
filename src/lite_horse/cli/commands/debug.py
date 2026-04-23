"""`litehorse debug` — placeholder group; `share` lands in Phase 30."""
from __future__ import annotations

import typer

app = typer.Typer(help="Debug helpers (share bundle lands in Phase 30).", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Root callback so typer builds a Group even with no subcommands yet."""
