"""Session-summariser worker — Phase 43.

Runs the :class:`Summarizer` side-agent over one finished session, writes
the resulting ``(topic, summary)`` to :class:`SessionSummaryRepo`, and
re-indexes the summary into the recall store (Phase 42) so future
sessions can retrieve it semantically.

The queue contract:

    {"kind": "summarize",
     "user_id": "...",
     "agent_id": "...",
     "session_id": "...",
     "model": "<override or empty for default>"}

``kind="summarize"`` is the discriminator the dispatcher in
:mod:`lite_horse.worker.runner` uses to route this message ahead of the
cron path.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from lite_horse.agent.backends.recall_cloud import RecallCloudBackend
from lite_horse.agent.summarizer import Summarizer
from lite_horse.constants.models import MODEL_GPT_5_4_MINI
from lite_horse.providers.embedding import (
    EmbeddingProvider,
    select_embedding_provider,
)
from lite_horse.repositories.message_repo import MessageRepo
from lite_horse.repositories.session_repo import SessionRepo
from lite_horse.repositories.session_summary_repo import SessionSummaryRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

SUMMARIZE_KIND = "summarize"
DEFAULT_SUMMARIZER_MODEL = MODEL_GPT_5_4_MINI


@dataclass(frozen=True)
class SummarizeMessage:
    """One queued summarisation: one session for one (user, agent)."""

    kind: str
    user_id: str
    agent_id: str
    session_id: str
    model: str

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        model: str = "",
    ) -> SummarizeMessage:
        return cls(
            kind=SUMMARIZE_KIND,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            model=model,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SummarizeMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            session_id=str(data["session_id"]),
            model=str(data.get("model") or ""),
        )


def is_summarize_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == SUMMARIZE_KIND


async def run_summarize(
    message: SummarizeMessage,
    *,
    summarizer: Summarizer | None = None,
    embedder: EmbeddingProvider | None = None,
) -> bool:
    """Summarise one session and persist the row + recall chunk.

    Returns ``True`` on success (caller deletes the SQS message) or on
    a benign no-op (no messages, side-agent returned an empty record).
    A ``False`` return triggers SQS redelivery.
    """
    async with db_session(message.user_id, message.agent_id) as session:
        sess_repo = SessionRepo(session)
        meta = await sess_repo.get_session_meta(message.session_id)
        if meta is None:
            log.info(
                "summarize: session %s not found for user %s (already deleted?)",
                message.session_id,
                message.user_id,
            )
            return True
        messages = await MessageRepo(session).get_messages(message.session_id)

    if not messages:
        log.info(
            "summarize: session %s has no messages, skipping",
            message.session_id,
        )
        return True

    model = message.model or DEFAULT_SUMMARIZER_MODEL
    side_agent = summarizer or Summarizer(model=model)
    try:
        result = await side_agent.run(messages=messages)
    except Exception:
        log.exception(
            "summarize: side-agent raised for session %s", message.session_id
        )
        return False

    if not result.summary.strip():
        log.info(
            "summarize: empty result for session %s, skipping write",
            message.session_id,
        )
        return True

    async with db_session(message.user_id, message.agent_id) as session:
        await SessionSummaryRepo(session).upsert(
            session_id=message.session_id,
            agent_id=message.agent_id,
            summary=result.summary,
            topic=result.topic or None,
            generator=model,
        )

    # Best-effort recall index — failures don't fail the worker run since
    # the row is already durable in session_summaries.
    recall = RecallCloudBackend(
        user_id=message.user_id,
        agent_id=message.agent_id,
        embedder=embedder or select_embedding_provider(),
    )
    try:
        await recall.index(
            source_kind="session_summary",
            source_id=message.session_id,
            content=_compose_indexable(result.topic, result.summary),
        )
    except Exception:
        log.exception(
            "summarize: recall index failed for session %s",
            message.session_id,
        )
    return True


def _compose_indexable(topic: str, summary: str) -> str:
    """Combine topic + summary into the chunk text the recall index sees."""
    if topic.strip():
        return f"{topic.strip()}: {summary.strip()}"
    return summary.strip()
