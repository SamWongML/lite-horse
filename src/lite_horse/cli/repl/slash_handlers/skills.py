"""`/skills` (list / picker) + `/skill <slug>` (hint-activate next turn).

Both delegate to :mod:`lite_horse.cli.commands.skills` for the actual reads.
``/skill <slug>`` stages a text attachment referencing the skill so the next
user turn carries the hint without re-engineering the agent plumbing.
"""
from __future__ import annotations

from typing import Any

from lite_horse.cli.repl.slash import (
    SlashCommand,
    SlashOutcome,
    SlashRegistry,
)


async def _skills_list(args: list[str], state: Any) -> SlashOutcome:
    del args  # no arguments accepted
    printer = getattr(state, "print_line", print)

    from lite_horse.cli.commands import skills as skills_cmd

    rows = skills_cmd.list_skills()
    if not rows:
        printer("[skills] none installed")
        return SlashOutcome.CONTINUE
    for r in rows:
        version = f" v{r['version']}" if r.get("version") is not None else ""
        desc = r.get("description") or ""
        printer(f"  {r['name']}{version}  — {desc}" if desc else f"  {r['name']}{version}")
    return SlashOutcome.CONTINUE


async def _skill_hint(args: list[str], state: Any) -> SlashOutcome:
    """Stage a hint that the next turn should consider the named skill."""
    printer = getattr(state, "print_line", print)
    if not args:
        printer("[skill] usage: /skill <slug>")
        return SlashOutcome.CONTINUE
    slug = args[0].strip()

    from lite_horse.cli.commands import skills as skills_cmd

    data = skills_cmd.show_skill(slug)
    if data is None:
        printer(f"[skill] no skill {slug!r}")
        return SlashOutcome.CONTINUE
    hint = (
        f"[skill-hint] Consider calling `skill_view(name={slug!r})` if this "
        "turn's task relates to the skill."
    )
    state.pending_attachments.append({"kind": "text", "content": hint})
    printer(f"[skill] hinted {slug!r} for the next turn")
    return SlashOutcome.CONTINUE


def register(reg: SlashRegistry) -> None:
    reg.register(SlashCommand(
        name="skills",
        summary="list installed skills",
        handler=_skills_list,
    ))
    reg.register(SlashCommand(
        name="skill",
        summary="hint-activate a skill for the next turn",
        handler=_skill_hint,
    ))
