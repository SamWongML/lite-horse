"""`litehorse doctor` — diagnose environment and config."""
from __future__ import annotations

from typing import Any

import typer

app = typer.Typer(help="Diagnose environment, DB, API key, and MCP setup.")


def _check_state_dir() -> dict[str, Any]:
    from lite_horse.cli._settings import state_dir

    home = state_dir()
    return {"name": "state_dir", "path": str(home), "exists": home.exists()}


def _check_config() -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Load config (auto-materializing defaults) and report.

    `load_config()` creates `~/.litehorse/config.yaml` on first read, so
    doctor's "config_file" check is really a load-and-validate gate.
    """
    from lite_horse.cli._settings import state_dir

    config_path = state_dir() / "config.yaml"
    try:
        from lite_horse.config import load_config

        cfg = load_config()
        mcp = [s.name for s in cfg.mcp_servers]
    except Exception as exc:
        return (
            {
                "name": "config_file",
                "path": str(config_path),
                "ok": False,
                "error": str(exc),
            },
            {"name": "mcp_servers", "configured": []},
            False,
        )
    return (
        {"name": "config_file", "path": str(config_path), "ok": True},
        {"name": "mcp_servers", "configured": mcp},
        True,
    )


def _check_db() -> tuple[dict[str, Any], bool]:
    import sqlite3

    from lite_horse.cli._settings import state_dir

    db_path = state_dir() / "sessions.db"
    check: dict[str, Any] = {"name": "sessions_db", "path": str(db_path), "ok": True}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        check["ok"] = False
        check["error"] = str(exc)
        return check, False
    return check, True


def _check_api_key() -> tuple[dict[str, Any], bool]:
    import os

    key = os.environ.get("OPENAI_API_KEY", "")
    present = bool(key)
    return (
        {
            "name": "openai_api_key",
            "present": present,
            "shape_ok": key.startswith(("sk-", "sk_")) if present else False,
        },
        present,
    )


@app.callback(invoke_without_command=True)
def run(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON to stdout."),
) -> None:
    """Report on state directory, DB, OpenAI key, and configured MCP servers."""
    from lite_horse.cli._output import emit_error, emit_item, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    checks: list[dict[str, Any]] = []
    ok = True

    state = _check_state_dir()
    checks.append(state)
    ok = ok and bool(state["exists"])

    cfg_check, mcp_check, cfg_ok = _check_config()
    checks.extend([cfg_check, mcp_check])
    ok = ok and cfg_ok

    db_check, db_ok = _check_db()
    checks.append(db_check)
    ok = ok and db_ok

    key_check, key_ok = _check_api_key()
    checks.append(key_check)
    ok = ok and key_ok

    for c in checks:
        emit_item(c, json_mode=json_mode)
    emit_result({"ok": ok} if json_mode else ("ok" if ok else "degraded"), json_mode=json_mode)

    if not ok:
        if not json_mode:
            emit_error("one or more checks failed", code=int(ExitCode.CONFIG), json_mode=False)
        raise typer.Exit(code=int(ExitCode.CONFIG))
