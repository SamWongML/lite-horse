"""Rich panels for tool calls + y/n/A/N approval prompt.

The approval prompt is a standalone function used by any code path that
wants to gate a tool invocation on user consent. Phase 28 wires it to
``/permission`` state and exposes it from the REPL; actually blocking the
SDK's tool execution at the pre-dispatch boundary is scoped to a later
phase (see ``docs/plans/v0.3-cli-entrypoint.md``).

Heavy imports (``rich``, ``prompt_toolkit``) stay inside function bodies so
``litehorse --help`` remains under the fast-path budget.
"""
from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import Any

# Number of lines shown in the collapsed panel.
COLLAPSED_LINE_COUNT = 6


class ApprovalDecision(enum.Enum):
    YES = "yes"
    NO = "no"
    ALWAYS = "always"
    NEVER = "never"


@dataclass
class ToolCallRecord:
    """One announce/output pair rendered by :class:`ToolCallPanel`."""

    name: str
    arguments: str
    output: str | None = None
    expanded: bool = False


@dataclass
class ToolCallPanel:
    """Keeps the last N tool calls so Ctrl-O can expand them retroactively."""

    records: list[ToolCallRecord] = field(default_factory=list)
    max_records: int = 20

    def announce(self, name: str, arguments: str) -> ToolCallRecord:
        rec = ToolCallRecord(name=name, arguments=arguments)
        self.records.append(rec)
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]
        return rec

    def attach_output(self, name: str, output: str) -> ToolCallRecord | None:
        """Match ``output`` to the most recent unresolved call with this name."""
        for rec in reversed(self.records):
            if rec.name == name and rec.output is None:
                rec.output = output
                return rec
        return None

    def expand_last(self) -> ToolCallRecord | None:
        if not self.records:
            return None
        rec = self.records[-1]
        rec.expanded = True
        return rec


def render_tool_announce(record: ToolCallRecord) -> Any:
    """Dim announcement line, printed as the call arrives."""
    from rich.text import Text

    t = Text()
    t.append("→ ", style="dim cyan")
    t.append(record.name, style="bold cyan")
    preview = _shorten(record.arguments, 80)
    if preview:
        t.append(f"  {preview}", style="dim")
    return t


def render_tool_output(record: ToolCallRecord, *, expanded: bool) -> Any:
    """Collapsed or expanded panel of the tool result."""
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.text import Text

    body: Any
    output = record.output or ""
    if _looks_like_diff(output):
        body = Syntax(output, "diff", theme="ansi_dark", background_color="default")
    elif _looks_like_json(output):
        pretty = _pretty_json(output)
        body = Syntax(
            pretty if expanded else _first_lines(pretty, COLLAPSED_LINE_COUNT),
            "json",
            theme="ansi_dark",
            background_color="default",
        )
    else:
        text = output if expanded else _first_lines(output, COLLAPSED_LINE_COUNT)
        body = Text(text)

    title = f"↩ {record.name}"
    suffix = "" if expanded else "  (Ctrl-O to expand)"
    return Panel(body, title=title + suffix, border_style="dim", expand=False)


async def prompt_approval(name: str, arguments: str) -> ApprovalDecision:
    """Inline y/n/A/N prompt. Returns the user's decision.

    Uses ``prompt_toolkit.shortcuts.prompt`` in async form so it cooperates
    with any outer event loop. Defaults to ``NO`` on non-interactive input
    (EOF or cancelled session) — refuse-by-default is the safe posture.
    """
    from prompt_toolkit import PromptSession

    session: PromptSession[str] = PromptSession()
    question = (
        f"approve tool '{name}'? "
        f"args: {_shorten(arguments, 60)}\n"
        f"  (y)es / (n)o / (A)lways / (N)ever > "
    )
    try:
        answer = await session.prompt_async(question)
    except (EOFError, KeyboardInterrupt):
        return ApprovalDecision.NO
    answer = (answer or "").strip()
    if answer == "A":
        return ApprovalDecision.ALWAYS
    if answer == "N":
        return ApprovalDecision.NEVER
    lower = answer.lower()
    if lower in {"y", "yes"}:
        return ApprovalDecision.YES
    return ApprovalDecision.NO


def _shorten(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _first_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[:n]) + "\n…"


def _looks_like_diff(s: str) -> bool:
    stripped = s.lstrip()
    return stripped.startswith(("--- ", "+++ ", "@@ ", "diff --git"))


def _looks_like_json(s: str) -> bool:
    s = s.strip()
    return (s.startswith("{") and s.endswith("}")) or (
        s.startswith("[") and s.endswith("]")
    )


def _pretty_json(s: str) -> str:
    try:
        return json.dumps(json.loads(s), indent=2, sort_keys=False)
    except (json.JSONDecodeError, TypeError):
        return s
