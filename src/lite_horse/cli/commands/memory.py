"""`litehorse memory {show, clear, compact}` — inspect MEMORY.md / USER.md.

The `--user` flag switches from the agent's MEMORY.md to the USER.md profile
store. Same two files the runtime reads at session start. `compact` runs the
v0.4 :class:`Consolidator` against MEMORY.md when utilisation crosses 0.8.
"""
from __future__ import annotations

import asyncio

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


async def _run_compact_async(*, model: str) -> dict[str, object]:
    from lite_horse.agent.consolidator import Consolidator
    from lite_horse.config import load_config
    from lite_horse.constants import ENTRY_DELIMITER
    from lite_horse.worker.compact import COMPACT_UTILIZATION_THRESHOLD

    store = _store("memory")
    before = store.entries()
    if not before:
        return {"status": "noop", "reason": "empty", "before_chars": 0}
    chars_before = store.total_chars()
    utilization = chars_before / store.char_limit
    if utilization <= COMPACT_UTILIZATION_THRESHOLD:
        return {
            "status": "noop",
            "reason": "under_threshold",
            "utilization": utilization,
            "before_chars": chars_before,
        }
    cfg = load_config()
    chosen_model = model or cfg.model
    consolidator = Consolidator(model=chosen_model)
    trajectory = [{"role": "memory", "content": e} for e in before]
    new_entries = await consolidator.run(turn_input=trajectory)
    if not new_entries:
        return {
            "status": "noop",
            "reason": "consolidator_empty",
            "before_chars": chars_before,
        }
    new_body = ENTRY_DELIMITER.join(new_entries)
    if len(new_body) >= chars_before:
        return {
            "status": "noop",
            "reason": "not_shorter",
            "before_chars": chars_before,
            "after_chars": len(new_body),
        }
    store.path.write_text(new_body + "\n", encoding="utf-8")
    return {
        "status": "compacted",
        "model": chosen_model,
        "before_entries": len(before),
        "after_entries": len(new_entries),
        "before_chars": chars_before,
        "after_chars": len(new_body),
    }


@app.command("compact")
def compact_cmd(
    model: str = typer.Option(
        "", "--model", help="Override the model used by the Consolidator."
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Merge similar entries in MEMORY.md when utilisation crosses 0.8."""
    from lite_horse.cli._output import emit_result

    result = asyncio.run(_run_compact_async(model=model))
    if json_mode:
        emit_result(result, json_mode=True)
        return
    status = result.get("status")
    if status != "compacted":
        emit_result(
            f"memory compact: no-op ({result.get('reason')})", json_mode=False
        )
        return
    emit_result(
        "memory compact: {b}→{a} chars, {be}→{ae} entries".format(
            b=result["before_chars"],
            a=result["after_chars"],
            be=result["before_entries"],
            ae=result["after_entries"],
        ),
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
