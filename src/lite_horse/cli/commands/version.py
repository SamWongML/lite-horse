"""`litehorse version` — filled in commit 4."""
from __future__ import annotations

import typer

app = typer.Typer(help="Show the installed lite-horse version.")


@app.callback(invoke_without_command=True)
def _not_implemented() -> None:
    raise SystemExit("version command arrives in a follow-up commit")
