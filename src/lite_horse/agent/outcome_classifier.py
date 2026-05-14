"""Outcome classifier side-agent.

Reads the tail of a finished turn (final assistant text + last few tool
outputs) and produces a ``{rating, reason}`` JSON record. The caller —
the cloud worker or the CLI REPL — writes the result through the
tenant's :class:`FeedbackSink` with ``source='classifier'``.

The classifier is intentionally narrow: it returns one rating in
``{-1, 0, 1}`` and never asks for tool calls. Two SDK turns is enough for
a single model round-trip + retry if the first response isn't parseable;
failures are surfaced as ``rating=0`` ("no signal") rather than as
exceptions so the worker never re-queues a bad classification.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agents import Agent, Runner

log = logging.getLogger(__name__)

CLASSIFIER_MAX_TURNS = 2
CLASSIFIER_MAX_TAIL_MESSAGES = 12
CLASSIFIER_MAX_REASON_CHARS = 240

_CLASSIFIER_INSTRUCTIONS = (
    "You read the tail of a completed agent <-> user turn and assign a "
    "single outcome rating.\n\n"
    "Return STRICT JSON, no prose, no code fences:\n"
    '  {"rating": <-1 | 0 | 1>, "reason": "<= 240 chars, plain text>"}\n\n'
    "Rules:\n"
    "- rating=1 means the agent visibly resolved the user's request: a "
    "concrete answer, a successful tool sequence, a written-down decision.\n"
    "- rating=-1 means the agent visibly failed: a returned error, an "
    "abandoned tool loop, a refusal that didn't address the request.\n"
    "- rating=0 means the signal is ambiguous (small talk, partial "
    "progress with no clear outcome, or you cannot tell from the tail).\n"
    "- The reason must point at concrete evidence from the trajectory "
    "(e.g. 'github tool returned 404' / 'agent wrote memory then "
    "answered'). No generic praise.\n"
    "- If the trajectory is empty or contains no user message, return "
    'rating=0 with reason="no trajectory".\n'
)


@dataclass(frozen=True)
class ClassifierResult:
    """Side-agent output. ``rating=0`` is the safe no-signal default."""

    rating: int
    reason: str


class OutcomeClassifier:
    """Side-agent that maps a turn tail to ``{rating, reason}``."""

    def __init__(
        self,
        *,
        model: str,
        max_turns: int = CLASSIFIER_MAX_TURNS,
        max_messages: int = CLASSIFIER_MAX_TAIL_MESSAGES,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.max_messages = max_messages

    async def run(
        self,
        *,
        user_request: str | None,
        final_text: str | None,
        tool_tail: list[dict[str, Any]] | None = None,
    ) -> ClassifierResult:
        """Classify one turn. Never raises — failures collapse to rating=0."""
        tail = self._tail(tool_tail)
        if not (user_request or final_text or tail):
            return ClassifierResult(rating=0, reason="no trajectory")
        agent = Agent(
            name="outcome-classifier",
            model=self.model,
            instructions=_CLASSIFIER_INSTRUCTIONS,
        )
        prompt = json.dumps(
            {
                "user_request": (user_request or "")[:500],
                "final_text": (final_text or "")[:800],
                "tool_tail": tail,
            },
            default=str,
        )
        try:
            result = await Runner.run(agent, prompt, max_turns=self.max_turns)
        except Exception:
            log.exception("outcome-classifier: side-agent run failed")
            return ClassifierResult(rating=0, reason="classifier raised")
        output = getattr(result, "final_output", None)
        if not output:
            return ClassifierResult(rating=0, reason="empty response")
        return _parse(str(output))

    def _tail(
        self, tool_tail: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]]:
        if not tool_tail:
            return []
        substantive = [
            t
            for t in tool_tail
            if isinstance(t, dict) and t.get("name") and t.get("output")
        ]
        return substantive[-self.max_messages :]


def _parse(output: str) -> ClassifierResult:
    stripped = output.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").lstrip("json").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        log.warning("outcome-classifier: failed to parse JSON response")
        return ClassifierResult(rating=0, reason="unparseable")
    if not isinstance(data, dict):
        return ClassifierResult(rating=0, reason="unparseable")
    rating = data.get("rating", 0)
    try:
        rating_int = int(rating)
    except (TypeError, ValueError):
        rating_int = 0
    if rating_int not in (-1, 0, 1):
        rating_int = 0
    reason = str(data.get("reason") or "").strip()[
        :CLASSIFIER_MAX_REASON_CHARS
    ]
    return ClassifierResult(rating=rating_int, reason=reason)
