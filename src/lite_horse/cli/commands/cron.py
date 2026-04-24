"""`litehorse cron {list, add, enable, disable, remove, run-once, scheduler}`.

`list` / `add` / `enable` / `disable` / `remove` delegate to the same
:func:`lite_horse.cron.manage_tool.dispatch` helper the ``cron_manage``
tool uses — single source of truth for schedule/delivery validation.

`run-once` fires one job's closure immediately (development aid). `scheduler`
replaces the old ``python -c "from lite_horse.cron.scheduler import ..."``
invocation and shuts down cleanly on SIGINT / SIGTERM.
"""
from __future__ import annotations

from typing import Any

import typer

app = typer.Typer(
    help="Manage and run scheduled cron jobs.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


# ---------- pure helpers (shared with slash handlers) ----------

def list_jobs() -> list[dict[str, Any]]:
    from lite_horse.cron.manage_tool import dispatch

    result = dispatch("list")
    return list(result.get("jobs", []))


def add_job(
    *,
    schedule: str,
    prompt: str,
    delivery_platform: str = "log",
    delivery_url: str | None = None,
) -> dict[str, Any]:
    from lite_horse.cron.manage_tool import dispatch

    return dispatch(
        "add",
        schedule=schedule,
        prompt=prompt,
        delivery_platform=delivery_platform,
        delivery_url=delivery_url,
    )


def set_enabled(job_id: str, *, enabled: bool) -> dict[str, Any]:
    from lite_horse.cron.manage_tool import dispatch

    return dispatch("enable" if enabled else "disable", job_id=job_id)


def remove_job(job_id: str) -> dict[str, Any]:
    from lite_horse.cron.manage_tool import dispatch

    return dispatch("remove", job_id=job_id)


async def run_once(job_id: str) -> dict[str, Any]:
    """Fire one job's closure on the current event loop. Useful for dev only."""
    from lite_horse.config import load_config
    from lite_horse.cron.jobs import JobStore
    from lite_horse.cron.scheduler import make_fire
    from lite_horse.sessions.db import SessionDB
    from lite_horse.sessions.search_tool import bind_db

    store = JobStore()
    job = store.get(job_id)
    if job is None:
        return {"success": False, "error": f"no such job: {job_id}"}
    cfg = load_config()
    db = SessionDB()
    bind_db(db)
    fire = make_fire(db=db, cfg=cfg, store=store)
    await fire(job)
    return {"success": True, "job_id": job_id}


# ---------- Typer commands ----------

@app.command("list")
def list_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print every stored cron job."""
    from lite_horse.cli._output import emit_item, emit_result

    jobs = list_jobs()
    for j in jobs:
        emit_item(j, json_mode=json_mode)
    emit_result({"count": len(jobs)} if json_mode else f"{len(jobs)} jobs", json_mode=json_mode)


@app.command("add")
def add_cmd(
    schedule: str = typer.Argument(..., help="Crontab (5 fields) or @alias."),
    prompt: str = typer.Argument(..., help="User prompt the agent runs on each firing."),
    platform: str = typer.Option("log", "--platform", help="Delivery platform: log | webhook."),
    url: str | None = typer.Option(
        None, "--url", help="Webhook URL (required for --platform webhook)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Create a new cron job."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    result = add_job(
        schedule=schedule,
        prompt=prompt,
        delivery_platform=platform,
        delivery_url=url,
    )
    if not result.get("success"):
        emit_error(str(result.get("error") or "add failed"),
                   code=int(ExitCode.USAGE), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.USAGE))
    emit_result(
        result["job"] if json_mode else f"added: {result['job']['id']}",
        json_mode=json_mode,
    )


@app.command("enable")
def enable_cmd(
    job_id: str = typer.Argument(..., help="Job id from `cron list`."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    _flip(job_id, enabled=True, json_mode=json_mode)


@app.command("disable")
def disable_cmd(
    job_id: str = typer.Argument(..., help="Job id from `cron list`."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    _flip(job_id, enabled=False, json_mode=json_mode)


def _flip(job_id: str, *, enabled: bool, json_mode: bool) -> None:
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    result = set_enabled(job_id, enabled=enabled)
    if not result.get("success"):
        emit_error(str(result.get("error") or "update failed"),
                   code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    verb = "enabled" if enabled else "disabled"
    emit_result({"id": job_id, "enabled": enabled} if json_mode else f"{verb}: {job_id}",
                json_mode=json_mode)


@app.command("remove")
def remove_cmd(
    job_id: str = typer.Argument(..., help="Job id from `cron list`."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Delete one cron job."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    result = remove_job(job_id)
    if not result.get("success"):
        emit_error(str(result.get("error") or "remove failed"),
                   code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    emit_result({"id": job_id, "removed": True} if json_mode else f"removed: {job_id}",
                json_mode=json_mode)


@app.command("run-once")
def run_once_cmd(
    job_id: str = typer.Argument(..., help="Job id to fire once."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Fire one scheduled job immediately (no scheduling). Dev aid."""
    import asyncio

    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    result = asyncio.run(run_once(job_id))
    if not result.get("success"):
        emit_error(str(result.get("error") or "run-once failed"),
                   code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    emit_result(result if json_mode else f"fired: {job_id}", json_mode=json_mode)


@app.command("scheduler")
def scheduler_cmd() -> None:
    """Run the APScheduler loop in the foreground until SIGINT / SIGTERM."""
    from lite_horse.cron.scheduler import run_scheduler_blocking

    run_scheduler_blocking()
