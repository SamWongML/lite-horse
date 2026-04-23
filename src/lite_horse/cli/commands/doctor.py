"""`litehorse doctor` — filled in commit 4."""
from __future__ import annotations

import typer

app = typer.Typer(help="Diagnose environment, DB, and API key setup.")


@app.callback(invoke_without_command=True)
def _not_implemented() -> None:
    raise SystemExit("doctor command arrives in a follow-up commit")
