"""`litehorse sessions {list, show, search, end, cleanup}`.

Thin Typer wrappers over :class:`lite_horse.sessions.local.LocalSessionRepo`.
The slash-command handlers in :mod:`lite_horse.cli.repl.slash_handlers.session`
(resume picker) and the upcoming REPL `/sessions` mirror import the same
helpers below — single source of truth, no duplicate SQL.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

import typer

app = typer.Typer(
    help="Inspect, search, and manage persisted sessions.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


# ---------- pure helpers (shared with slash handlers) ----------

def list_sessions(*, limit: int = 20) -> list[dict[str, Any]]:
    from lite_horse.sessions.local import LocalSessionRepo

    db = LocalSessionRepo()
    return db.list_recent_sessions(limit=limit)


def show_session(session_id: str) -> dict[str, Any] | None:
    """Return the session row + every message. ``None`` if unknown id."""
    from lite_horse.sessions.local import LocalSessionRepo

    db = LocalSessionRepo()
    rows = db.list_recent_sessions(limit=10000)
    match = next((r for r in rows if r["id"] == session_id), None)
    if match is None:
        return None
    match["messages"] = db.get_messages(session_id)
    return match


def search(query: str, *, limit: int = 20, source: str | None = None) -> list[dict[str, Any]]:
    from lite_horse.sessions.local import LocalSessionRepo

    db = LocalSessionRepo()
    hits = db.search_messages(
        query,
        limit=min(max(1, int(limit)), 50),
        source_filter=[source] if source else None,
    )
    return [asdict(h) for h in hits]


def end(session_id: str, *, reason: str = "user_exit") -> bool:
    """Stamp ``ended_at`` on one session. Returns True if the row existed."""
    from lite_horse.sessions.local import LocalSessionRepo

    db = LocalSessionRepo()
    existing = db.list_recent_sessions(limit=10000)
    if not any(r["id"] == session_id for r in existing):
        return False
    db.end_session(session_id, end_reason=reason)
    return True


def cleanup(*, days: int) -> int:
    """Delete sessions that ended more than ``days`` ago. Returns count deleted."""
    from lite_horse.sessions.local import LocalSessionRepo

    cutoff = time.time() - max(0, int(days)) * 86400.0
    db = LocalSessionRepo()
    return db.delete_sessions_ended_before(cutoff)


# ---------- Typer commands ----------

@app.command("list")
def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows (1-500)."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Show the N most-recent sessions, newest first."""
    from lite_horse.cli._output import emit_item, emit_result

    capped = min(max(1, int(limit)), 500)
    rows = list_sessions(limit=capped)
    for r in rows:
        emit_item(r, json_mode=json_mode)
    emit_result({"count": len(rows)} if json_mode else f"{len(rows)} sessions", json_mode=json_mode)


@app.command("show")
def show_cmd(
    key: str = typer.Argument(..., help="Full session key."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print the session row + full transcript."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    match = show_session(key)
    if match is None:
        emit_error(f"no session with id {key!r}", code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    emit_result(match, json_mode=json_mode)


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="FTS5 query string."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max hits (1-50)."),
    source: str | None = typer.Option(
        None, "--source", help="Filter by source (e.g. web, cli, cron)."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Full-text search across every message."""
    from lite_horse.cli._output import emit_item, emit_result

    hits = search(query, limit=limit, source=source)
    for h in hits:
        emit_item(h, json_mode=json_mode)
    emit_result({"count": len(hits)} if json_mode else f"{len(hits)} hits", json_mode=json_mode)


@app.command("end")
def end_cmd(
    key: str = typer.Argument(..., help="Session key to mark ended."),
    reason: str = typer.Option("user_exit", "--reason", help="Recorded in ``end_reason``."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Stamp ``ended_at`` so the session is excluded from "active" views."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    if not end(key, reason=reason):
        emit_error(f"no session with id {key!r}", code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    emit_result({"id": key, "ended": True} if json_mode else f"ended: {key}", json_mode=json_mode)


@app.command("cleanup")
def cleanup_cmd(
    days: int = typer.Option(30, "--days", "-d", help="Delete ended sessions older than N days."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Permanently delete sessions that ended > ``--days`` ago."""
    from lite_horse.cli._output import emit_result
    from lite_horse.cli.exit_codes import ExitCode

    if days < 0:
        raise typer.Exit(code=int(ExitCode.USAGE))
    if not yes and not json_mode:
        typer.confirm(f"Delete every ended session older than {days} days?", abort=True)
    removed = cleanup(days=days)
    emit_result(
        {"deleted": removed} if json_mode else f"deleted {removed} sessions",
        json_mode=json_mode,
    )
