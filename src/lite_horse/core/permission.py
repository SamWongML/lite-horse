"""Per-session tool-permission policy shared by the CLI REPL and ``api.py``.

Lives under ``core/`` instead of ``cli/repl/`` so the API surface can import
it without breaking the "``lite_horse.api`` must not transitively load
``lite_horse.cli``" isolation invariant.

Three modes:

- ``auto`` — every tool is offered to the model.
- ``ask``  — every tool call is visible; ``allowed_tools`` / ``denied_tools``
  memoize per-session decisions. (Inline y/n/A/N prompting during a live
  stream is intentionally left for a follow-up phase; mode is a data
  contract here.)
- ``ro``   — write tools are filtered out at agent-build time so the model
  cannot invoke them at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Tools that mutate durable state. ``ro`` mode filters these out at
# agent-build time (see ``lite_horse.agent.factory.build_agent``).
WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "memory",
    "skill_manage",
    "cron_manage",
})


VALID_MODES: frozenset[str] = frozenset({"auto", "ask", "ro"})


@dataclass
class PermissionPolicy:
    """Mutable permission state for one session.

    ``allowed_tools`` / ``denied_tools`` are used by ``ask`` mode to remember
    the "always yes" / "always no" decisions made during this session.
    """

    mode: str = "auto"
    allowed_tools: set[str] = field(default_factory=set)
    denied_tools: set[str] = field(default_factory=set)

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Decide whether to offer ``tool_name`` to the model.

        Only ``ro`` filters at build time; ``ask`` leaves tools enabled so
        the model still sees them — the decision happens at call time in
        a later phase.
        """
        if self.mode == "ro":
            return tool_name not in WRITE_TOOL_NAMES
        return True


# Process-wide policy registry keyed by ``session_key``. The REPL writes here
# from ``/permission``; callers of ``api.run_turn_streaming`` pass the session
# key and the api resolves the policy on each turn.
_POLICIES: dict[str, PermissionPolicy] = {}


def set_policy(session_key: str, policy: PermissionPolicy) -> None:
    _POLICIES[session_key] = policy


def get_policy(session_key: str) -> PermissionPolicy | None:
    return _POLICIES.get(session_key)


def clear_policy(session_key: str) -> None:
    _POLICIES.pop(session_key, None)


def normalize_mode(raw: str) -> str | None:
    """Map ``read-only`` / ``ro`` / ``readonly`` onto canonical ``ro``.

    Returns ``None`` on unknown input so callers can surface a hint to the
    user instead of silently defaulting.
    """
    lower = raw.strip().lower()
    if lower in {"ro", "read-only", "readonly"}:
        return "ro"
    if lower in VALID_MODES:
        return lower
    return None


def filter_tools(tools: list[Any], policy: PermissionPolicy) -> list[Any]:
    """Return ``tools`` with names blocked by ``policy`` removed.

    ``Tool`` has a ``name`` attribute on every concrete subclass we use;
    anything without one is kept (conservative).
    """
    return [t for t in tools if policy.is_tool_allowed(getattr(t, "name", ""))]
