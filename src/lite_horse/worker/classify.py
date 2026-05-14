"""Classify worker.

Runs the :class:`OutcomeClassifier` side-agent over one finished turn
and writes a ``turn_outcomes`` row with ``source='classifier'`` via
:class:`FeedbackCloudBackend`. The queue contract:

    {"kind": "classify",
     "user_id": "...",
     "agent_id": "...",
     "session_id": "...",
     "turn_id": "...",
     "user_request": "...",
     "final_text": "...",
     "tool_tail": [...],
     "skill_slug": "...",
     "model": "..."}

``kind="classify"`` is the discriminator the dispatcher in
:mod:`lite_horse.worker.runner` uses. Failures collapse to
``rating=0`` rather than re-queue; classification is best-effort and
must never block the user-visible turn.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from lite_horse.agent.backends.feedback_cloud import FeedbackCloudBackend
from lite_horse.agent.outcome_classifier import (
    ClassifierResult,
    OutcomeClassifier,
)

log = logging.getLogger(__name__)

CLASSIFY_KIND = "classify"
DEFAULT_CLASSIFIER_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class ClassifyMessage:
    """One queued classification: one turn for one ``(user, agent)``."""

    kind: str
    user_id: str
    agent_id: str
    session_id: str
    turn_id: str
    user_request: str = ""
    final_text: str = ""
    tool_tail: list[dict[str, Any]] = field(default_factory=list)
    skill_slug: str | None = None
    model: str = ""

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        turn_id: str,
        user_request: str = "",
        final_text: str = "",
        tool_tail: list[dict[str, Any]] | None = None,
        skill_slug: str | None = None,
        model: str = "",
    ) -> ClassifyMessage:
        return cls(
            kind=CLASSIFY_KIND,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            turn_id=turn_id,
            user_request=user_request,
            final_text=final_text,
            tool_tail=list(tool_tail or []),
            skill_slug=skill_slug,
            model=model,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), default=str)

    @classmethod
    def from_json(cls, raw: str) -> ClassifyMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            session_id=str(data["session_id"]),
            turn_id=str(data["turn_id"]),
            user_request=str(data.get("user_request") or ""),
            final_text=str(data.get("final_text") or ""),
            tool_tail=list(data.get("tool_tail") or []),
            skill_slug=data.get("skill_slug"),
            model=str(data.get("model") or ""),
        )


def is_classify_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == CLASSIFY_KIND


async def run_classify(
    message: ClassifyMessage,
    *,
    classifier: OutcomeClassifier | None = None,
) -> bool:
    """Classify one turn and persist the outcome row.

    Returns ``True`` on success (the SQS message is acked) or on any
    benign no-op. ``False`` is reserved for transient DB write failures.
    """
    model = message.model or DEFAULT_CLASSIFIER_MODEL
    clf = classifier or OutcomeClassifier(model=model)
    result: ClassifierResult = await clf.run(
        user_request=message.user_request or None,
        final_text=message.final_text or None,
        tool_tail=message.tool_tail or None,
    )
    sink = FeedbackCloudBackend(
        user_id=message.user_id, agent_id=message.agent_id
    )
    try:
        await sink.record(
            session_id=message.session_id,
            turn_id=message.turn_id,
            source="classifier",
            rating=result.rating,
            reason=result.reason or None,
            skill_slug=message.skill_slug,
        )
    except Exception:
        log.exception(
            "classify: failed to persist outcome (user=%s turn=%s)",
            message.user_id,
            message.turn_id,
        )
        return False
    return True
