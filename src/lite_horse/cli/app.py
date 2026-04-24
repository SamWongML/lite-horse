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
@click.option("--session", "session_key", default=None,
              help="Bind the REPL / one-shot to this existing session key.")
def repl(prompt: tuple[str, ...], session_key: str | None) -> None:
    """Open the interactive REPL (or one-shot if a prompt is given).

    - ``litehorse``                 → REPL on a fresh session
    - ``litehorse "write a haiku"`` → one-shot, prints the answer, exits
    - ``echo hi | litehorse``       → one-shot from piped stdin
    - ``litehorse --session <key>`` → REPL bound to an existing session
    """
    import asyncio
    import sys

    from lite_horse.cli._tty import detect
    from lite_horse.cli.repl.loop import main_loop

    joined = " ".join(prompt) if prompt else None
    tty = detect()
    stdin_text: str | None = None
    if joined is None and not tty.stdin_tty:
        piped = sys.stdin.read().strip()
        if piped:
            stdin_text = piped

    rc = asyncio.run(main_loop(
        prompt=joined,
        stdin_text=stdin_text,
        session_key=session_key,
    ))
    raise SystemExit(rc)


def _attach_typer_commands() -> None:
    """Register Typer-based subtrees onto the Click root group.

    Imports happen inside the function so `--help` on the root group does
    not pay for any subcommand body's transitive imports.
    """
    import typer

    from lite_horse.cli.commands import completion as completion_cmd
    from lite_horse.cli.commands import config as config_cmd
    from lite_horse.cli.commands import cron as cron_cmd
    from lite_horse.cli.commands import debug as debug_cmd
    from lite_horse.cli.commands import doctor as doctor_cmd
    from lite_horse.cli.commands import logs as logs_cmd
    from lite_horse.cli.commands import memory as memory_cmd
    from lite_horse.cli.commands import sessions as sessions_cmd
    from lite_horse.cli.commands import skills as skills_cmd
    from lite_horse.cli.commands import version as version_cmd

    for name, app in (
        ("version", version_cmd.app),
        ("doctor", doctor_cmd.app),
        ("config", config_cmd.app),
        ("completion", completion_cmd.app),
        ("debug", debug_cmd.app),
        ("sessions", sessions_cmd.app),
        ("skills", skills_cmd.app),
        ("cron", cron_cmd.app),
        ("memory", memory_cmd.app),
        ("logs", logs_cmd.app),
    ):
        cli.add_command(typer.main.get_command(app), name)


def _is_help_invocation(argv: list[str]) -> bool:
    """True iff argv looks like a pure ``--help`` request.

    We bypass logging bootstrap in that case so ``rich`` stays unloaded
    and the fast-path test stays green. Anything else pays the bootstrap
    cost exactly once.
    """
    if "--help" in argv or "-h" in argv:
        return True
    return False


def _bootstrap_logging(argv: list[str]) -> None:
    """Install CLI log handlers unless this is a fast-path ``--help`` run."""
    if _is_help_invocation(argv):
        return
    from lite_horse.cli._logging import configure
    from lite_horse.cli._settings import load

    settings = load()
    json_mode = "--json" in argv or settings.json_output
    configure(json_mode=json_mode, debug=settings.debug)


def main() -> None:
    import sys

    _bootstrap_logging(sys.argv[1:])
    _attach_typer_commands()
    cli()


if __name__ == "__main__":  # pragma: no cover - exercised by completion subprocess
    main()
