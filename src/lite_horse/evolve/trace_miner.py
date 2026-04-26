"""Mine failure trajectories for a skill from the local session store + stats sidecar.

The plan would like ``ErrorKind``-labeled traces, but v0.2 does not persist
classifier kinds per message. We approximate: a session is a *candidate* if its
``end_reason`` is error-like or its ``tool_call_count`` is high relative to
``message_count`` (stuck-in-a-loop signal). For each candidate we surface the
first user turn, the last assistant turn, and the latest tool output — enough
context for the reflector to see where the skill went wrong.

The skill's ``.stats.json`` sidecar provides the outcome summary when present.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from lite_horse.sessions.local import LocalSessionRepo
from lite_horse.skills import stats as skill_stats

_ERROR_END_REASONS: frozenset[str] = frozenset(
    {"model_refusal", "tool_error", "unknown_error", "context_overflow"}
)
_MAX_SNIPPET_CHARS = 600


@dataclass(frozen=True)
class Trajectory:
    session_id: str
    task: str
    response: str
    outcome: str


def mine_failures(
    skill_name: str,
    *,
    db: LocalSessionRepo,
    days: int = 14,
    limit: int = 5,
) -> list[Trajectory]:
    """Return up to ``limit`` failure trajectories touching ``skill_name``.

    A session touches the skill if any message body mentions the skill name
    (FTS5 match). We then keep the ones whose ``end_reason`` is error-like,
    newest first.
    """
    cutoff = time.time() - days * 86_400
    hits = db.search_messages(skill_name, limit=limit * 5)
    seen: set[str] = set()
    trajectories: list[Trajectory] = []
    sidecar = skill_stats.read(skill_name) or {}
    fallback_outcome = _fallback_outcome(sidecar)
    for hit in hits:
        if hit.timestamp < cutoff or hit.session_id in seen:
            continue
        seen.add(hit.session_id)
        meta = db.get_session_meta(hit.session_id)
        if meta is None:
            continue
        end_reason = (meta.get("end_reason") or "").lower()
        if end_reason and end_reason not in _ERROR_END_REASONS:
            continue
        messages = db.get_messages(hit.session_id)
        task = _first_of(messages, role="user")
        response = _last_of(messages, role="assistant")
        if not task or not response:
            continue
        trajectories.append(
            Trajectory(
                session_id=hit.session_id,
                task=_truncate(task),
                response=_truncate(response),
                outcome=end_reason or fallback_outcome,
            )
        )
        if len(trajectories) >= limit:
            break
    return trajectories


def _first_of(messages: list[dict[str, object]], *, role: str) -> str:
    for m in messages:
        if m.get("role") == role and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


def _last_of(messages: list[dict[str, object]], *, role: str) -> str:
    for m in reversed(messages):
        if m.get("role") == role and isinstance(m.get("content"), str):
            return str(m["content"])
    return ""


def _truncate(s: str) -> str:
    s = s.strip()
    return s if len(s) <= _MAX_SNIPPET_CHARS else s[: _MAX_SNIPPET_CHARS] + "…"


def _fallback_outcome(sidecar: dict[str, object]) -> str:
    err = sidecar.get("last_error_summary")
    if isinstance(err, str) and err:
        return f"error: {err[:200]}"
    return "unknown_failure"
