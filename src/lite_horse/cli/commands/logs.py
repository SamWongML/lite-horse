"""`litehorse logs {tail, path}` — inspect the stderr log file.

Phase 29 lays the surface. Phase 30 wires a ``RichHandler`` that actually
populates ``~/.litehorse/litehorse.log``; until then the file may be empty
or absent, and ``tail`` simply reports nothing. ``path`` always resolves.
"""
from __future__ import annotations

import typer

app = typer.Typer(
    help="Tail or locate the litehorse log file.",
    no_args_is_help=True,
)

_LOG_FILENAME = "litehorse.log"


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


# ---------- pure helpers ----------

def log_path() -> str:
    from lite_horse.cli._settings import state_dir

    return str(state_dir() / _LOG_FILENAME)


def tail_lines(*, n: int) -> list[str]:
    """Return the last ``n`` log lines. Empty list if the file is absent."""
    import pathlib

    p = pathlib.Path(log_path())
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8", errors="replace") as f:
        # Small log file in practice (rotated by the host). Read fully, slice.
        lines = f.readlines()
    return [line.rstrip("\n") for line in lines[-max(0, int(n)):]]


# ---------- Typer commands ----------

@app.command("tail")
def tail_cmd(
    n: int = typer.Option(50, "-n", "--lines", help="Lines to print (1-10000)."),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Stream appended lines until SIGINT."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print the tail of the log; optionally follow appended lines."""
    from lite_horse.cli._output import emit_item, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    capped = min(max(1, int(n)), 10_000)
    for line in tail_lines(n=capped):
        emit_item(line, json_mode=json_mode)

    if not follow:
        emit_result({"file": log_path()} if json_mode else log_path(), json_mode=json_mode)
        return

    import pathlib
    import time

    p = pathlib.Path(log_path())
    # Open-or-wait; follow mode keeps polling so logs appearing later show up.
    try:
        fh = p.open("r", encoding="utf-8", errors="replace") if p.exists() else None
        if fh is not None:
            fh.seek(0, 2)
        while True:
            if fh is None:
                if p.exists():
                    fh = p.open("r", encoding="utf-8", errors="replace")
                    fh.seek(0, 2)
                else:
                    time.sleep(0.5)
                    continue
            line = fh.readline()
            if not line:
                time.sleep(0.25)
                continue
            emit_item(line.rstrip("\n"), json_mode=json_mode)
    except KeyboardInterrupt:
        raise typer.Exit(code=int(ExitCode.SIGINT)) from None


@app.command("path")
def path_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print the log file path (does not create it)."""
    from lite_horse.cli._output import emit_result

    p = log_path()
    emit_result({"path": p} if json_mode else p, json_mode=json_mode)
