"""``litehorse agent {ls, create, use, show}`` — local multi-agent CLI.

Phase 41 introduces the ``agents`` axis. On the cloud the source of
truth is the Postgres ``agents`` table; the CLI mirror lives under
``~/.litehorse/agents/<slug>/`` so a Mac user can run
``litehorse --agent coder "..."`` next to ``litehorse --agent shopper
"..."`` against the same ``~/.litehorse/`` without booting any cloud
service.

Layout::

    ~/.litehorse/
    ├── agents/
    │   └── <slug>/
    │       ├── persona.txt        (optional persona text)
    │       ├── memory.md          (per-agent memory; mirrors v0.4 path)
    │       ├── user.md            (per-agent user profile)
    │       ├── skills/            (per-agent skills tree)
    │       └── jobs.json          (per-agent cron schedule)
    └── current_agent              (text file holding the active slug)

The active slug is read by :func:`current_agent` (REPL + tools call this
to pick the right :func:`agent_home`). On first run the legacy v0.4
flat layout is migrated by symlinking ``default`` to the legacy paths
so manual edits don't disappear.
"""
from __future__ import annotations

from pathlib import Path

import typer

from lite_horse.constants import litehorse_home

app = typer.Typer(
    help="Manage per-agent personas, memory, and skill bundles.",
    no_args_is_help=True,
)


_DEFAULT_SLUG = "default"
_LEGACY_LINKS = ("memory.md", "user.md", "skills", "jobs.json")


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


def _agents_root() -> Path:
    return litehorse_home() / "agents"


def _current_agent_file() -> Path:
    return litehorse_home() / "current_agent"


def agent_home(slug: str | None = None) -> Path:
    """Return ``~/.litehorse/agents/<slug>/`` (creating it on demand).

    ``slug=None`` → the current agent, falling back to ``default``.
    """
    chosen = slug or current_agent()
    home = _agents_root() / chosen
    home.mkdir(parents=True, exist_ok=True)
    return home


def current_agent() -> str:
    """Return the active agent slug, defaulting to ``default``.

    Honors ``LITEHORSE_AGENT`` env override; otherwise reads
    ``~/.litehorse/current_agent``; otherwise falls back to
    ``default``.
    """
    import os

    env = os.environ.get("LITEHORSE_AGENT")
    if env:
        return env.strip()
    f = _current_agent_file()
    if f.exists():
        text = f.read_text(encoding="utf-8").strip()
        if text:
            return text
    return _DEFAULT_SLUG


def set_current_agent(slug: str) -> None:
    """Persist ``slug`` as the active agent for subsequent runs."""
    home = litehorse_home()
    home.mkdir(parents=True, exist_ok=True)
    _current_agent_file().write_text(slug, encoding="utf-8")


def list_local_agents() -> list[dict[str, str | bool]]:
    """Enumerate local agents (each ``~/.litehorse/agents/<slug>/`` dir)."""
    root = _agents_root()
    if not root.exists():
        return []
    current = current_agent()
    out: list[dict[str, str | bool]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        persona = ""
        persona_path = d / "persona.txt"
        if persona_path.exists():
            persona = persona_path.read_text(encoding="utf-8").strip()
        out.append(
            {
                "slug": d.name,
                "persona": persona,
                "is_current": d.name == current,
            }
        )
    return out


def ensure_default_layout() -> None:
    """Create ``agents/default/`` with symlinks to the legacy v0.4 paths.

    Idempotent: if the user already migrated, or already has a default
    agent dir, this is a no-op. Symlinks (not copies) so manual edits to
    ``~/.litehorse/memory.md`` keep working until the user moves to a
    non-default agent.
    """
    home = litehorse_home()
    default_dir = home / "agents" / _DEFAULT_SLUG
    default_dir.mkdir(parents=True, exist_ok=True)
    for name in _LEGACY_LINKS:
        legacy = home / name
        target = default_dir / name
        if target.exists() or target.is_symlink():
            continue
        if legacy.exists():
            try:
                target.symlink_to(legacy)
            except OSError:
                # Fall through silently — Windows w/o developer mode, etc.
                pass


def create_local_agent(
    slug: str, *, persona: str = "", default_model: str | None = None
) -> Path:
    """Create ``~/.litehorse/agents/<slug>/`` and seed empty docs."""
    home = _agents_root() / slug
    if home.exists():
        raise FileExistsError(f"agent {slug!r} already exists at {home}")
    home.mkdir(parents=True, exist_ok=True)
    (home / "memory.md").write_text("", encoding="utf-8")
    (home / "user.md").write_text("", encoding="utf-8")
    (home / "skills").mkdir(exist_ok=True)
    if persona:
        (home / "persona.txt").write_text(persona, encoding="utf-8")
    if default_model:
        (home / "model").write_text(default_model, encoding="utf-8")
    return home


# ---------- Typer commands ----------


@app.command("ls")
def ls_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """List every agent under ``~/.litehorse/agents/``."""
    from lite_horse.cli._output import emit_item, emit_result

    ensure_default_layout()
    rows = list_local_agents()
    for r in rows:
        emit_item(r, json_mode=json_mode)
    emit_result(
        {"count": len(rows)} if json_mode else f"{len(rows)} agents",
        json_mode=json_mode,
    )


@app.command("create")
def create_cmd(
    slug: str = typer.Argument(..., help="Short agent slug (e.g. 'coder')."),
    persona: str = typer.Option(
        "", "--persona", help="Free-text persona block injected into prompts."
    ),
    default_model: str | None = typer.Option(
        None,
        "--model",
        help="Override the model id when this agent is active.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Create a new agent under ``~/.litehorse/agents/<slug>/``."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    ensure_default_layout()
    try:
        home = create_local_agent(
            slug, persona=persona, default_model=default_model
        )
    except FileExistsError as exc:
        emit_error(
            str(exc), code=int(ExitCode.USAGE), json_mode=json_mode
        )
        raise typer.Exit(code=int(ExitCode.USAGE)) from exc
    emit_result(
        {"slug": slug, "home": str(home)}
        if json_mode
        else f"created agent {slug!r} at {home}",
        json_mode=json_mode,
    )


@app.command("use")
def use_cmd(
    slug: str = typer.Argument(..., help="Slug to make active."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Persist ``slug`` as the default agent for subsequent runs."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    ensure_default_layout()
    home = _agents_root() / slug
    if not home.exists():
        emit_error(
            f"unknown agent {slug!r}; use 'litehorse agent create' first",
            code=int(ExitCode.NOT_FOUND),
            json_mode=json_mode,
        )
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    set_current_agent(slug)
    emit_result(
        {"slug": slug} if json_mode else f"active agent: {slug}",
        json_mode=json_mode,
    )


@app.command("show")
def show_cmd(
    slug: str | None = typer.Argument(None, help="Slug to inspect (default: active)."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print one agent's home + persona."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    ensure_default_layout()
    chosen = slug or current_agent()
    home = _agents_root() / chosen
    if not home.exists():
        emit_error(
            f"unknown agent {chosen!r}",
            code=int(ExitCode.NOT_FOUND),
            json_mode=json_mode,
        )
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    persona_path = home / "persona.txt"
    persona = (
        persona_path.read_text(encoding="utf-8").strip()
        if persona_path.exists()
        else ""
    )
    payload = {
        "slug": chosen,
        "home": str(home),
        "persona": persona,
        "is_current": chosen == current_agent(),
    }
    emit_result(payload, json_mode=json_mode)
