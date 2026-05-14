"""Per-session summariser side-agent.

A small side-agent that reads the last N messages of a finished session
and emits a ``{topic, summary}`` JSON record. The summary survives into
the next session via :class:`SessionSummaryRepo` + the recall index;
the topic is the human-readable label rendered next to the summary in
the next system prompt's ``## Recent Sessions`` block.

The side-agent runs in the worker (cloud) or inline at REPL exit (CLI).
A 3-turn budget keeps the cost predictable; the model is configurable so
``haiku-4-5`` / ``gpt-5.4-mini`` can be the default but premium tiers
can choose their own.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agents import Agent, Runner

log = logging.getLogger(__name__)

SUMMARIZER_MAX_MESSAGES = 20
SUMMARIZER_MAX_SUMMARY_CHARS = 300
SUMMARIZER_MAX_TOPIC_CHARS = 80

_SUMMARIZER_INSTRUCTIONS = (
    "You read the tail of a completed agent <-> user conversation and "
    "produce a compact record so a future session can recall what was "
    "discussed.\n\n"
    "Return STRICT JSON with two keys and nothing else:\n"
    '  {"topic": "<= 80 chars, noun phrase>",\n'
    '   "summary": "<= 300 chars, 1-3 sentences, third-person"}\n\n'
    "Rules:\n"
    "- The topic must be a short noun phrase (e.g. 'Q3 deployment plan'), "
    "not a sentence.\n"
    "- The summary must mention what the user asked about, what the agent "
    "did, and any durable outcome (a decision, a written-down preference, "
    "a follow-up). Skip transient details and tool-call noise.\n"
    "- Refer to the human as 'the user' and the assistant as 'the agent'.\n"
    "- If the conversation is empty or contains no substantive content, "
    'return {"topic": "", "summary": ""}.\n'
)


@dataclass(frozen=True)
class SessionSummary:
    """Side-agent output. Both fields may be empty strings on no-op."""

    topic: str
    summary: str


class Summarizer:
    """Side-agent that distils a session tail into a topic + summary."""

    def __init__(
        self,
        *,
        model: str,
        max_turns: int = 3,
        max_messages: int = SUMMARIZER_MAX_MESSAGES,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.max_messages = max_messages

    async def run(
        self, *, messages: list[dict[str, Any]]
    ) -> SessionSummary:
        """Return a validated summary. Never raises — failures = no-op."""
        tail = self._tail(messages)
        if not tail:
            return SessionSummary(topic="", summary="")
        agent = Agent(
            name="summarizer",
            model=self.model,
            instructions=_SUMMARIZER_INSTRUCTIONS,
        )
        prompt = json.dumps({"messages": tail}, default=str)
        try:
            result = await Runner.run(agent, prompt, max_turns=self.max_turns)
        except Exception:
            log.exception("summarizer: side-agent run failed")
            return SessionSummary(topic="", summary="")
        output = getattr(result, "final_output", None)
        if not output:
            return SessionSummary(topic="", summary="")
        return _parse_summary(str(output))

    def _tail(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages:
            return []
        substantive = [
            m
            for m in messages
            if isinstance(m, dict)
            and m.get("role") in {"user", "assistant"}
            and m.get("content")
        ]
        return substantive[-self.max_messages :]


def _parse_summary(output: str) -> SessionSummary:
    """Tolerant JSON parse with field-length bounds."""
    stripped = output.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").lstrip("json").strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        log.warning("summarizer: failed to parse JSON response")
        return SessionSummary(topic="", summary="")
    if not isinstance(data, dict):
        return SessionSummary(topic="", summary="")
    topic = str(data.get("topic") or "").strip()[:SUMMARIZER_MAX_TOPIC_CHARS]
    summary = str(data.get("summary") or "").strip()[
        :SUMMARIZER_MAX_SUMMARY_CHARS
    ]
    return SessionSummary(topic=topic, summary=summary)
