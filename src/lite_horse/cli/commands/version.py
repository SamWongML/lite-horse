"""`litehorse version` — print the installed package version."""
from __future__ import annotations

import typer

app = typer.Typer(help="Show the installed lite-horse version.")


@app.callback(invoke_without_command=True)
def show(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON to stdout."),
) -> None:
    """Print the installed lite-horse version."""
    from importlib.metadata import PackageNotFoundError, version

    from lite_horse.cli._output import emit_result

    try:
        v = version("lite-horse")
    except PackageNotFoundError:  # pragma: no cover - only hits in an uninstalled tree
        v = "unknown"
    emit_result({"version": v} if json_mode else v, json_mode=json_mode)
