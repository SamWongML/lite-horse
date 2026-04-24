"""Model / permission / debug slash commands.

Registered via :func:`register` from ``slash_handlers.session`` so the plan's
"single source of truth for the registry" shape holds.
"""
from __future__ import annotations

import logging
from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)


async def _model(args: list[str], state: Any) -> SlashOutcome:
    """Show the active model, or switch to a new one.

    With no args, lists known candidates via the picker when the terminal
    supports it; otherwise prints the current selection. The picker set is
    conservative: the configured default plus any model names found in the
    session DB (so recently-used models are reachable without typing).
    """
    from lite_horse.api import _ensure_ready
    from lite_horse.cli.repl.picker import PickerItem, pick_one

    printer = getattr(state, "print_line", print)
    if args:
        new_model = args[0]
        state.model = new_model
        printer(f"[model] switched to {new_model}")
        return SlashOutcome.CONTINUE

    _db, _agent, cfg = await _ensure_ready()
    candidates = _candidate_models(cfg.model, state.model)
    items = [PickerItem(value=m, label=m) for m in candidates]
    try:
        chosen = await pick_one("Pick a model", items)
    except Exception as exc:
        printer(f"[model] current: {state.model} (picker unavailable: {exc})")
        return SlashOutcome.CONTINUE

    if chosen is None:
        printer(f"[model] current: {state.model}")
        return SlashOutcome.CONTINUE
    state.model = chosen
    printer(f"[model] switched to {chosen}")
    return SlashOutcome.CONTINUE


def _candidate_models(default_model: str, current: str) -> list[str]:
    """De-duplicated candidate list for the model picker."""
    seen: set[str] = set()
    out: list[str] = []
    for m in (current, default_model):
        if m and m != "—" and m not in seen:
            out.append(m)
            seen.add(m)
    return out


async def _permission(args: list[str], state: Any) -> SlashOutcome:
    """Show or switch permission mode: ``auto`` / ``ask`` / ``ro``."""
    from lite_horse.core.permission import (
        PermissionPolicy,
        normalize_mode,
        set_policy,
    )

    printer = getattr(state, "print_line", print)
    if not args:
        printer(
            f"[permission] mode: {state.permission_mode}  "
            f"(allowed: {sorted(state.allowed_tools) or '—'}, "
            f"denied: {sorted(state.denied_tools) or '—'})"
        )
        return SlashOutcome.CONTINUE

    normalized = normalize_mode(args[0])
    if normalized is None:
        printer(f"[permission] unknown mode: {args[0]!r} (try auto | ask | ro)")
        return SlashOutcome.CONTINUE

    state.permission_mode = normalized
    policy = PermissionPolicy(
        mode=normalized,
        allowed_tools=set(state.allowed_tools),
        denied_tools=set(state.denied_tools),
    )
    set_policy(state.session_key, policy)
    _explain_mode(printer, normalized)
    return SlashOutcome.CONTINUE


def _explain_mode(printer: Any, mode: str) -> None:
    if mode == "auto":
        printer("[permission] auto — every tool is offered to the model")
    elif mode == "ro":
        printer("[permission] ro — write tools (memory / skill_manage / "
                "cron_manage) are filtered out at agent-build time")
    elif mode == "ask":
        printer("[permission] ask — tool calls are surfaced verbosely; "
                "inline y/n/A/N approval arrives in a follow-up phase")


async def _debug(args: list[str], state: Any) -> SlashOutcome:
    """Toggle verbose (DEBUG-level) logging for the current process."""
    printer = getattr(state, "print_line", print)
    if args:
        target = args[0].strip().lower()
        if target in {"on", "true", "1", "yes"}:
            want = True
        elif target in {"off", "false", "0", "no"}:
            want = False
        else:
            printer(f"[debug] unknown toggle: {args[0]!r} (try on | off)")
            return SlashOutcome.CONTINUE
    else:
        want = not state.debug

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if want else logging.INFO)
    state.debug = want
    printer(f"[debug] {'on' if want else 'off'}")
    return SlashOutcome.CONTINUE


def register(reg: SlashRegistry) -> None:
    reg.register(SlashCommand(
        name="model",
        summary="show or switch the active model",
        handler=_model,
    ))
    reg.register(SlashCommand(
        name="permission",
        summary="show or switch permission mode (auto | ask | ro)",
        handler=_permission,
    ))
    reg.register(SlashCommand(
        name="debug",
        summary="toggle DEBUG-level logging",
        handler=_debug,
    ))
