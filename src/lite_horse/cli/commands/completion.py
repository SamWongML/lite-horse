"""`litehorse completion install <shell>` — print shell completion script.

We print to stdout rather than writing into the user's rc files directly —
the user is expected to redirect into their own shell config, e.g.::

    litehorse completion install bash >> ~/.bashrc
    litehorse completion install zsh  >> ~/.zshrc
    litehorse completion install fish >  ~/.config/fish/completions/litehorse.fish
"""
from __future__ import annotations

import typer

app = typer.Typer(help="Install shell completion for litehorse.", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """Group callback so `litehorse completion install <shell>` routes correctly."""


@app.command()
def install(shell: str = typer.Argument(..., help="bash | zsh | fish")) -> None:
    """Print the completion script for the given shell to stdout."""
    from click.shell_completion import BashComplete, FishComplete, ZshComplete

    from lite_horse.cli._output import emit_error
    from lite_horse.cli.app import _attach_typer_commands, cli
    from lite_horse.cli.exit_codes import ExitCode

    classes = {"bash": BashComplete, "zsh": ZshComplete, "fish": FishComplete}
    cls = classes.get(shell)
    if cls is None:
        emit_error(
            f"unknown shell {shell!r}; choose from {sorted(classes)}",
            code=int(ExitCode.USAGE),
            json_mode=False,
        )
        raise typer.Exit(code=int(ExitCode.USAGE))

    _attach_typer_commands()
    comp = cls(cli, {}, "litehorse", "_LITEHORSE_COMPLETE")
    print(comp.source())
