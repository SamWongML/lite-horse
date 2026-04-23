"""Entry point for the `litehorse` CLI.

`DefaultGroup` routes bare `litehorse` (no args) and `litehorse "prompt"`
(positional arg) to the `repl` command. Named subcommands (`version`,
`doctor`, `config`, `completion`, `debug`, and in later phases
`sessions`/`skills`/`cron`/`memory`/`logs`) attach as Typer subtrees via
`typer.main.get_command`.

Contract: nothing at module top imports openai, prompt_toolkit, or rich.
Subcommand bodies that need heavier runtime modules import them inside
the function.
"""
from __future__ import annotations

import click
from click_default_group import DefaultGroup


@click.group(
    cls=DefaultGroup,
    default="repl",
    default_if_no_args=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(package_name="lite-horse", prog_name="litehorse")
def cli() -> None:
    """litehorse — interactive-first CLI for the lite-horse runtime."""


@cli.command(context_settings={"ignore_unknown_options": True})  # type: ignore[untyped-decorator]
@click.argument("prompt", nargs=-1)
def repl(prompt: tuple[str, ...]) -> None:
    """Open the interactive REPL (or one-shot if a prompt is given)."""
    from lite_horse.cli.repl.loop import run_stub

    joined = " ".join(prompt) if prompt else None
    raise SystemExit(run_stub(joined))


def _attach_typer_commands() -> None:
    """Register Typer-based subtrees onto the Click root group.

    Imports happen inside the function so `--help` on the root group does
    not pay for any subcommand body's transitive imports.
    """
    import typer

    from lite_horse.cli.commands import completion as completion_cmd
    from lite_horse.cli.commands import config as config_cmd
    from lite_horse.cli.commands import debug as debug_cmd
    from lite_horse.cli.commands import doctor as doctor_cmd
    from lite_horse.cli.commands import version as version_cmd

    for name, app in (
        ("version", version_cmd.app),
        ("doctor", doctor_cmd.app),
        ("config", config_cmd.app),
        ("completion", completion_cmd.app),
        ("debug", debug_cmd.app),
    ):
        cli.add_command(typer.main.get_command(app), name)


def main() -> None:
    _attach_typer_commands()
    cli()


if __name__ == "__main__":  # pragma: no cover - exercised by completion subprocess
    main()
