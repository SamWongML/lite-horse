"""`litehorse config {show,path,edit}` — inspect and edit config.yaml."""
from __future__ import annotations

import typer

app = typer.Typer(help="Inspect or edit ~/.litehorse/config.yaml.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Group."""


@app.command()
def show(
    json_mode: bool = typer.Option(False, "--json", help="Emit the parsed config as NDJSON."),
) -> None:
    """Print the current config.

    Human mode prints the on-disk YAML verbatim. JSON mode parses, then
    emits one NDJSON record so downstream tools get a stable shape.
    """
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli._settings import state_dir
    from lite_horse.cli.exit_codes import ExitCode

    path = state_dir() / "config.yaml"
    if not path.exists():
        # Materialize defaults on first run so `show` is never surprising.
        from lite_horse.config import load_config

        load_config()

    if json_mode:
        from lite_horse.config import load_config

        try:
            cfg = load_config()
        except Exception as exc:
            emit_error(f"config load failed: {exc}", code=int(ExitCode.CONFIG), json_mode=True)
            raise typer.Exit(code=int(ExitCode.CONFIG)) from exc
        emit_result(cfg.model_dump(mode="json"), json_mode=True)
        return

    emit_result(path.read_text(encoding="utf-8").rstrip("\n"), json_mode=False)


@app.command()
def path(
    json_mode: bool = typer.Option(False, "--json", help="Emit as NDJSON."),
) -> None:
    """Print the config file path (does not create it)."""
    from lite_horse.cli._output import emit_result
    from lite_horse.cli._settings import state_dir

    p = state_dir() / "config.yaml"
    emit_result({"path": str(p)} if json_mode else str(p), json_mode=json_mode)


@app.command()
def edit() -> None:
    """Open the config file in $EDITOR (or $VISUAL, falling back to vi)."""
    import os
    import shlex
    import subprocess

    from lite_horse.cli._output import emit_error
    from lite_horse.cli._settings import state_dir
    from lite_horse.cli.exit_codes import ExitCode

    p = state_dir() / "config.yaml"
    if not p.exists():
        from lite_horse.config import load_config

        load_config()

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    cmd = [*shlex.split(editor), str(p)]
    try:
        result = subprocess.run(cmd, check=False)
    except FileNotFoundError:
        emit_error(f"editor not found: {editor!r}", code=int(ExitCode.IO), json_mode=False)
        raise typer.Exit(code=int(ExitCode.IO)) from None
    if result.returncode != 0:
        raise typer.Exit(code=int(ExitCode.GENERIC))
