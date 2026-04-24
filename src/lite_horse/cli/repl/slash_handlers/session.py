"""Foundation + session-management slash commands.

Foundation: ``/help``, ``/exit`` (+ aliases), ``/clear``.
Session management (Phase 28): ``/new``, ``/resume``, ``/fork``, ``/compact``,
``/share``.

All session-management handlers keep their heavy imports inside the handler
body so this module is cheap to import for ``--help``.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)


async def _help(args: list[str], state: Any) -> SlashOutcome:
    registry: SlashRegistry | None = getattr(state, "registry", None)
    printer = getattr(state, "print_line", print)
    if registry is None:
        printer("(no slash registry attached to state)")
        return SlashOutcome.CONTINUE
    printer("slash commands:")
    for cmd in registry.all_commands():
        alias_hint = (
            f"  (aliases: {', '.join('/' + a for a in cmd.aliases)})"
            if cmd.aliases else ""
        )
        printer(f"  /{cmd.name:<12} {cmd.summary}{alias_hint}")
    return SlashOutcome.CONTINUE


async def _exit(args: list[str], state: Any) -> SlashOutcome:
    return SlashOutcome.EXIT


async def _clear(args: list[str], state: Any) -> SlashOutcome:
    return SlashOutcome.CLEAR


def _fresh_cli_session_key() -> str:
    """A new session key for the interactive CLI.

    Matches ``lite_horse.core.session_key.build_session_key`` shape but
    embeds a short UUID so successive ``/new`` invocations don't collide.
    """
    from lite_horse.core.session_key import build_session_key

    return build_session_key(
        platform="cli", chat_type="repl", chat_id=f"local-{uuid.uuid4().hex[:8]}"
    )


async def _new(args: list[str], state: Any) -> SlashOutcome:
    """Start a fresh session; preserve model + permission; clear scrollback."""
    printer = getattr(state, "print_line", print)
    new_key = _fresh_cli_session_key()
    old_key = state.session_key
    state.session_key = new_key
    state.total_tokens = 0
    state.total_cost_usd = None

    # Carry forward the permission policy onto the new session key so
    # /permission is sticky across /new.
    from lite_horse.core.permission import (
        PermissionPolicy,
        clear_policy,
        set_policy,
    )

    policy = PermissionPolicy(
        mode=state.permission_mode,
        allowed_tools=set(state.allowed_tools),
        denied_tools=set(state.denied_tools),
    )
    clear_policy(old_key)
    set_policy(new_key, policy)

    printer(f"[new session] {new_key}")
    return SlashOutcome.CLEAR


async def _resume(args: list[str], state: Any) -> SlashOutcome:
    """Switch to an existing session (by prefix) or open a picker."""
    from lite_horse.api import _ensure_ready

    printer = getattr(state, "print_line", print)
    db, _agent, _cfg = await _ensure_ready()
    if args:
        return _resume_by_prefix(args[0], state, db, printer)
    return await _resume_via_picker(state, db, printer)


def _resume_by_prefix(prefix: str, state: Any, db: Any, printer: Any) -> SlashOutcome:
    try:
        resolved = db.find_session_by_prefix(prefix)
    except ValueError as exc:
        printer(f"[resume] {exc}")
        return SlashOutcome.CONTINUE
    if resolved is None:
        printer(f"[resume] no session matches prefix: {prefix!r}")
        return SlashOutcome.CONTINUE
    state.session_key = resolved
    printer(f"[resumed] {resolved}")
    return SlashOutcome.CLEAR


async def _resume_via_picker(state: Any, db: Any, printer: Any) -> SlashOutcome:
    from lite_horse.cli.repl.picker import PickerItem, pick_one

    recent = db.list_recent_sessions(limit=20)
    if not recent:
        printer("[resume] no sessions on record yet")
        return SlashOutcome.CONTINUE
    items = [
        PickerItem(value=r["id"], label=_format_session_label(r))
        for r in recent
    ]
    try:
        chosen = await pick_one("Resume which session?", items)
    except Exception as exc:  # picker unavailable (headless test harness)
        printer(f"[resume] picker unavailable: {exc}")
        return SlashOutcome.CONTINUE
    if chosen is None:
        printer("[resume] cancelled")
        return SlashOutcome.CONTINUE
    state.session_key = chosen
    printer(f"[resumed] {chosen}")
    return SlashOutcome.CLEAR


def _format_session_label(row: dict[str, Any]) -> str:
    """Human-readable one-line summary for the resume picker."""
    started = row.get("started_at")
    started_str = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(float(started)))
        if started else "?"
    )
    title = row.get("title") or row["id"]
    count = row.get("message_count") or 0
    return f"{started_str}  [{count} msgs]  {title}"


async def _fork(args: list[str], state: Any) -> SlashOutcome:
    """Branch the current session to a new key; switch to it."""
    from lite_horse.api import _ensure_ready
    from lite_horse.core.permission import (
        PermissionPolicy,
        set_policy,
    )

    printer = getattr(state, "print_line", print)
    db, _agent, cfg = await _ensure_ready()
    src = state.session_key
    dst = _fresh_cli_session_key()

    # Ensure destination exists.
    db.create_session(session_id=dst, source="cli", model=cfg.model)
    copied = db.copy_messages(src_session_id=src, dst_session_id=dst)
    state.session_key = dst

    # Copy permission policy onto the new key too.
    set_policy(dst, PermissionPolicy(
        mode=state.permission_mode,
        allowed_tools=set(state.allowed_tools),
        denied_tools=set(state.denied_tools),
    ))

    printer(f"[forked] {src} → {dst} ({copied} messages copied)")
    return SlashOutcome.CLEAR


async def _compact(args: list[str], state: Any) -> SlashOutcome:
    """Trigger compression-as-consolidation for the current session."""
    from lite_horse.agent.consolidator import Consolidator
    from lite_horse.api import _ensure_ready
    from lite_horse.memory.store import MemoryFull, MemoryStore, UnsafeMemoryContent

    printer = getattr(state, "print_line", print)
    db, _agent, cfg = await _ensure_ready()
    messages = db.get_messages(state.session_key)
    if not messages:
        printer("[compact] no messages in this session yet")
        return SlashOutcome.CONTINUE

    consolidator = Consolidator(model=cfg.model)
    try:
        entries = await consolidator.run(turn_input=messages)
    except Exception as exc:
        printer(f"[compact] consolidator failed: {exc}")
        return SlashOutcome.CONTINUE

    if not entries:
        printer("[compact] nothing worth persisting")
        return SlashOutcome.CONTINUE

    store = MemoryStore.for_memory()
    written = 0
    for entry in entries:
        try:
            store.add(entry)
            written += 1
        except (MemoryFull, UnsafeMemoryContent, ValueError) as exc:
            printer(f"[compact] skipped entry: {exc}")
    printer(f"[compact] {written} entr{'y' if written == 1 else 'ies'} added to MEMORY.md")
    return SlashOutcome.CONTINUE


async def _share(args: list[str], state: Any) -> SlashOutcome:
    """Stub: hand off to the Phase 30 ``debug share`` implementation.

    Wired now so ``/help`` shows it; flipping the body to the full bundler
    happens in Phase 30 without touching callers.
    """
    printer = getattr(state, "print_line", print)
    printer("[share] full session bundling arrives in Phase 30 "
            "(see: `litehorse debug share`).")
    return SlashOutcome.CONTINUE


def build_default_registry() -> SlashRegistry:
    """Register foundation + session-management slash commands."""
    reg = SlashRegistry()
    reg.register(SlashCommand(
        name="help",
        summary="show slash command reference",
        handler=_help,
        aliases=("h",),
    ))
    reg.register(SlashCommand(
        name="exit",
        summary="exit the REPL",
        handler=_exit,
        aliases=("quit", "q"),
    ))
    reg.register(SlashCommand(
        name="clear",
        summary="clear the scrollback",
        handler=_clear,
        aliases=("cls",),
    ))
    reg.register(SlashCommand(
        name="new",
        summary="start a fresh session (preserves model + permission)",
        handler=_new,
    ))
    reg.register(SlashCommand(
        name="resume",
        summary="resume a session by key prefix (or open picker)",
        handler=_resume,
    ))
    reg.register(SlashCommand(
        name="fork",
        summary="branch the current session to a new key",
        handler=_fork,
    ))
    reg.register(SlashCommand(
        name="compact",
        summary="distill this session into MEMORY.md now",
        handler=_compact,
    ))
    reg.register(SlashCommand(
        name="share",
        summary="export this session for debug sharing (Phase 30)",
        handler=_share,
    ))
    _register_model_handlers(reg)
    _register_tool_handlers(reg)
    _register_attachment_handlers(reg)
    _register_scripted_parity_handlers(reg)
    return reg


def _register_model_handlers(reg: SlashRegistry) -> None:
    """Late-import the model-group handlers to keep this file flat.

    A separate registration step keeps the wiring explicit instead of a
    decorator scan, which trips the import-isolation test.
    """
    from lite_horse.cli.repl.slash_handlers import model as model_module

    model_module.register(reg)


def _register_tool_handlers(reg: SlashRegistry) -> None:
    from lite_horse.cli.repl.slash_handlers import tools as tools_module

    tools_module.register(reg)


def _register_attachment_handlers(reg: SlashRegistry) -> None:
    from lite_horse.cli.repl.attachments import attach_handler, paste_image_handler

    reg.register(SlashCommand(
        name="attach",
        summary="stage a file or URL for the next turn",
        handler=attach_handler,
    ))
    reg.register(SlashCommand(
        name="paste-image",
        summary="attach an image from the system clipboard",
        handler=paste_image_handler,
    ))


def _register_scripted_parity_handlers(reg: SlashRegistry) -> None:
    """Register Phase-29 slash handlers that mirror the scripted subtrees.

    Each helper imports its ``commands/*`` counterpart lazily so ``--help``
    on the root CLI never transitively loads them.
    """
    from lite_horse.cli.repl.slash_handlers import cron as cron_module
    from lite_horse.cli.repl.slash_handlers import memory as memory_module
    from lite_horse.cli.repl.slash_handlers import skills as skills_module

    cron_module.register(reg)
    memory_module.register(reg)
    skills_module.register(reg)
