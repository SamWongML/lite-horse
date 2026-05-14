"""Backend Protocols + ``TenantContext`` carrier.

A per-turn dependency injection seam between the agent runtime and
durable state. Every tool that touches memory, skills, or cron goes
through one of the Protocols here; the SDK delivers the bundle via
``RunContextWrapper.context``. :class:`RecallBackend` provides semantic
recall via the ``memory_search`` tool.

Two impls per Protocol live next to it:
- ``*_local.py`` wraps the v0.4 filesystem code under ``~/.litehorse/``.
  Used by the CLI and by tests.
- ``*_cloud.py`` wraps the v0.4 repositories with per-call short-lived
  transactions. Used by the multi-tenant FastAPI path.

A typed :class:`TenantContext` is built once per turn at agent factory
time and never mutated after that.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lite_horse.agent.backends.cron import CronBackend, CronJobView
from lite_horse.agent.backends.cron_local import CronLocalBackend
from lite_horse.agent.backends.feedback import (
    FeedbackSink,
    OutcomeRecord,
    OutcomeSource,
    OutcomeStats,
)
from lite_horse.agent.backends.feedback_local import FeedbackLocalBackend
from lite_horse.agent.backends.memory import (
    MemoryBackend,
    MemoryFull,
    MemoryKind,
    UnsafeMemoryContent,
)
from lite_horse.agent.backends.memory_local import MemoryLocalBackend
from lite_horse.agent.backends.recall import RecallBackend, Recalled, SourceKind
from lite_horse.agent.backends.recall_local import RecallLocalBackend
from lite_horse.agent.backends.skill import SkillBackend
from lite_horse.agent.backends.skill_local import SkillLocalBackend


@dataclass(frozen=True)
class TenantContext:
    """Per-turn tenant bundle threaded through ``RunContextWrapper.context``.

    ``user_id`` / ``agent_id`` are ``None`` in the CLI / single-user path
    where there is no authenticated tenant. ``agent_id`` is populated
    once the multi-agent table is in play.

    ``recall`` provides semantic recall over the user's memory + summary
    + skill history; the agent reaches it via the ``memory_search`` tool.
    """

    user_id: str | None
    agent_id: str | None
    memory: MemoryBackend
    skill: SkillBackend
    cron: CronBackend
    recall: RecallBackend
    feedback: FeedbackSink


def build_local_tenant_context() -> TenantContext:
    """Construct a single-user :class:`TenantContext` over the local FS.

    Used by the CLI / REPL path and as a fallback when tools/hooks run
    without an explicit ``RunContextWrapper.context`` (notably under
    legacy unit tests that pass ``ctx=None``).
    """
    return TenantContext(
        user_id=None,
        agent_id=None,
        memory=MemoryLocalBackend(),
        skill=SkillLocalBackend(),
        cron=CronLocalBackend(),
        recall=RecallLocalBackend(),
        feedback=FeedbackLocalBackend(),
    )


def resolve_tenant(ctx: Any) -> TenantContext:
    """Read the tenant bundle from ``ctx.context`` or fall back to local.

    The fallback exists for legacy unit tests that drive tool bodies and
    hooks without going through ``Runner.run_streamed`` — production
    paths always pass a :class:`TenantContext` via the ``context=`` kwarg
    so ``ctx.context`` is populated.
    """
    if ctx is not None:
        candidate = getattr(ctx, "context", None)
        if isinstance(candidate, TenantContext):
            return candidate
    return build_local_tenant_context()


__all__ = [
    "CronBackend",
    "CronJobView",
    "FeedbackSink",
    "MemoryBackend",
    "MemoryFull",
    "MemoryKind",
    "OutcomeRecord",
    "OutcomeSource",
    "OutcomeStats",
    "RecallBackend",
    "Recalled",
    "SkillBackend",
    "SourceKind",
    "TenantContext",
    "UnsafeMemoryContent",
    "build_local_tenant_context",
    "resolve_tenant",
]
