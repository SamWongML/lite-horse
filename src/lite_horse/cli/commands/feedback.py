"""`litehorse feedback <turn_id> --rating ±1` — Phase 44.

Writes one ``source='user_explicit'`` row to the local NDJSON feedback
log via :class:`FeedbackLocalBackend`. Mirrors the cloud HTTP route
``POST /v1/turns/{turn_id}/feedback`` for CLI parity. No DB required —
the local backend handles its own path under ``~/.litehorse/``.
"""
from __future__ import annotations

import asyncio

import typer

app = typer.Typer(
    help="Record a user-explicit outcome for one finished turn.",
    no_args_is_help=True,
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def feedback_cmd(
    ctx: typer.Context,
    turn_id: str = typer.Argument(
        None, help="The turn UUID returned by the prior run."
    ),
    rating: int = typer.Option(
        ...,
        "--rating",
        "-r",
        help="One of -1, 0, +1.",
    ),
    reason: str = typer.Option(
        None, "--reason", help="<= 240 chars; explains the rating."
    ),
    session_key: str = typer.Option(
        None,
        "--session",
        help="The session_key the turn ran under (default: 'cli').",
    ),
    skill_slug: str = typer.Option(
        None, "--skill", help="Attribute the rating to one skill slug."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Record one explicit outcome row for a finished turn."""
    if ctx.invoked_subcommand is not None:
        return
    from lite_horse.agent.backends.feedback_local import FeedbackLocalBackend
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    if turn_id is None:
        emit_error(
            "turn_id is required", code=int(ExitCode.USAGE), json_mode=json_mode
        )
        raise typer.Exit(code=int(ExitCode.USAGE))
    if rating not in (-1, 0, 1):
        emit_error(
            "rating must be one of -1, 0, +1",
            code=int(ExitCode.USAGE),
            json_mode=json_mode,
        )
        raise typer.Exit(code=int(ExitCode.USAGE))

    backend = FeedbackLocalBackend()

    async def _go() -> None:
        await backend.record(
            session_id=session_key or "cli",
            turn_id=turn_id,
            source="user_explicit",
            rating=rating,
            reason=reason,
            skill_slug=skill_slug,
        )

    asyncio.run(_go())
    emit_result(
        {
            "turn_id": turn_id,
            "rating": rating,
            "source": "user_explicit",
            "path": str(backend.path),
        },
        json_mode=json_mode,
    )
