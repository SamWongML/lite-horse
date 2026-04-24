"""`litehorse memory {show, clear}` — inspect MEMORY.md / USER.md.

The `--user` flag switches from the agent's MEMORY.md to the USER.md profile
store. Same two files the runtime reads at session start.
"""
from __future__ import annotations

import typer

app = typer.Typer(
    help="Inspect or wipe the agent's memory stores.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


# ---------- pure helpers (shared with slash handlers) ----------

def _store(which: str):  # type: ignore[no-untyped-def]
    from lite_horse.memory.store import MemoryStore

    if which == "user":
        return MemoryStore.for_user()
    return MemoryStore.for_memory()


def read_entries(*, user: bool = False) -> dict[str, object]:
    store = _store("user" if user else "memory")
    entries = store.entries()
    return {
        "label": store.label,
        "path": str(store.path),
        "char_limit": store.char_limit,
        "total_chars": store.total_chars(),
        "entries": entries,
    }


def clear_entries(*, user: bool = False) -> int:
    """Delete every entry from the selected store. Returns count removed."""
    store = _store("user" if user else "memory")
    existing = store.entries()
    store.path.write_text("", encoding="utf-8")
    return len(existing)


# ---------- Typer commands ----------

@app.command("show")
def show_cmd(
    user: bool = typer.Option(False, "--user", help="Show USER.md instead of MEMORY.md."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print the memory entries, one per line."""
    from lite_horse.cli._output import emit_item, emit_result

    data = read_entries(user=user)
    if json_mode:
        emit_result(data, json_mode=True)
        return
    entries = data["entries"]
    assert isinstance(entries, list)
    if not entries:
        emit_result(f"[{data['label']}] (empty)", json_mode=False)
        return
    for e in entries:
        emit_item(str(e), json_mode=False)
    emit_result(
        f"{data['label']}: {data['total_chars']}/{data['char_limit']} chars, "
        f"{len(entries)} entries",
        json_mode=False,
    )


@app.command("clear")
def clear_cmd(
    user: bool = typer.Option(False, "--user", help="Clear USER.md instead of MEMORY.md."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Delete every entry in the selected memory store."""
    from lite_horse.cli._output import emit_result

    label = "USER.md" if user else "MEMORY.md"
    if not yes and not json_mode:
        typer.confirm(f"Delete every entry in {label}?", abort=True)
    removed = clear_entries(user=user)
    human = f"cleared {removed} entries from {label}"
    emit_result(
        {"cleared": removed, "file": label} if json_mode else human,
        json_mode=json_mode,
    )
