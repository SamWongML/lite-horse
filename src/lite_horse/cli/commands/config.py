"""`litehorse config {show,path,edit}` — filled in commit 5."""
from __future__ import annotations

import typer

app = typer.Typer(help="Inspect or edit ~/.litehorse/config.yaml.", no_args_is_help=True)


@app.command()
def show() -> None:  # pragma: no cover - replaced in commit 5
    raise SystemExit("config show arrives in a follow-up commit")


@app.command()
def path() -> None:  # pragma: no cover - replaced in commit 5
    raise SystemExit("config path arrives in a follow-up commit")


@app.command()
def edit() -> None:  # pragma: no cover - replaced in commit 5
    raise SystemExit("config edit arrives in a follow-up commit")
