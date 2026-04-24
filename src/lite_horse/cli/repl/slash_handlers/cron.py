"""`/cron [list|add|enable|disable|remove]` slash commands.

Every handler imports the corresponding helper from
:mod:`lite_horse.cli.commands.cron` — no duplicate schedule/delivery
validation in the REPL. Side effects (JSON persistence) live in the shared
helper so the CLI and slash paths behave identically.
"""
from __future__ import annotations

from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)

_SUBCOMMANDS = ("list", "add", "enable", "disable", "remove")


async def _cron(args: list[str], state: Any) -> SlashOutcome:
    printer = getattr(state, "print_line", print)
    if not args:
        printer("[cron] usage: /cron list | add <schedule> <prompt> | "
                "enable <id> | disable <id> | remove <id>")
        return SlashOutcome.CONTINUE

    sub = args[0].strip().lower()
    if sub not in _SUBCOMMANDS:
        printer(f"[cron] unknown subcommand: {sub!r} (try: {', '.join(_SUBCOMMANDS)})")
        return SlashOutcome.CONTINUE

    from lite_horse.cli.commands import cron as cron_cmd

    if sub == "list":
        _handle_list(printer, cron_cmd)
        return SlashOutcome.CONTINUE
    if sub == "add":
        return await _handle_add(args[1:], printer, cron_cmd)
    _handle_idop(sub, args[1:], printer, cron_cmd)
    return SlashOutcome.CONTINUE


def _handle_list(printer: Any, cron_cmd: Any) -> None:
    jobs = cron_cmd.list_jobs()
    if not jobs:
        printer("[cron] no jobs configured")
        return
    for j in jobs:
        flag = "on " if j.get("enabled") else "off"
        printer(f"  {j['id']}  [{flag}]  {j['schedule']!r:<18}  {j['prompt']!r}")


def _handle_idop(sub: str, rest: list[str], printer: Any, cron_cmd: Any) -> None:
    """``enable | disable | remove`` — all take a single job_id argument."""
    if not rest:
        printer(f"[cron] {sub} needs a job id")
        return
    job_id = rest[0]
    if sub == "enable":
        result = cron_cmd.set_enabled(job_id, enabled=True)
    elif sub == "disable":
        result = cron_cmd.set_enabled(job_id, enabled=False)
    else:  # remove
        result = cron_cmd.remove_job(job_id)
    if not result.get("success"):
        printer(f"[cron] {result.get('error')}")
    else:
        printer(f"[cron] {sub}d: {job_id}")


async def _handle_add(
    remaining: list[str],
    printer: Any,
    cron_cmd: Any,
) -> SlashOutcome:
    """``/cron add <schedule> <prompt...>`` with optional --url for webhook."""
    if len(remaining) < 2:
        printer("[cron] add needs: <schedule> <prompt...>")
        return SlashOutcome.CONTINUE
    schedule = remaining[0]
    # Extract --url if present; everything else joins as the prompt text.
    url: str | None = None
    prompt_parts: list[str] = []
    it = iter(remaining[1:])
    for token in it:
        if token == "--url":
            url = next(it, None)
        else:
            prompt_parts.append(token)
    prompt = " ".join(prompt_parts).strip()
    if not prompt:
        printer("[cron] add needs a non-empty prompt")
        return SlashOutcome.CONTINUE
    platform = "webhook" if url else "log"
    result = cron_cmd.add_job(
        schedule=schedule,
        prompt=prompt,
        delivery_platform=platform,
        delivery_url=url,
    )
    if not result.get("success"):
        printer(f"[cron] {result.get('error')}")
    else:
        printer(f"[cron] added: {result['job']['id']}")
    return SlashOutcome.CONTINUE


def register(reg: SlashRegistry) -> None:
    reg.register(SlashCommand(
        name="cron",
        summary="list/add/enable/disable/remove cron jobs",
        handler=_cron,
    ))
