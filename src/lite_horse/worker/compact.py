"""Memory.md compaction worker — Phase 43.

Runs the v0.4 :class:`Consolidator` against an over-full ``memory.md`` to
merge similar entries and drop transient noise, then writes the compacted
body back through :class:`MemoryRepo`. Triggered by the daily compact
tick whenever utilisation crosses 0.8.

Queue contract::

    {"kind": "compact",
     "user_id": "...",
     "agent_id": "...",
     "model": "<override or empty for default>"}

A successful run reduces utilisation under 0.8 (the plan's acceptance
threshold) and re-indexes the new chunks via the recall backend so the
condensed entries stay searchable.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from lite_horse.agent.backends.recall_cloud import RecallCloudBackend
from lite_horse.agent.consolidator import Consolidator
from lite_horse.constants import ENTRY_DELIMITER
from lite_horse.constants.models import MODEL_GPT_5_4_MINI
from lite_horse.providers.embedding import (
    EmbeddingProvider,
    select_embedding_provider,
)
from lite_horse.repositories.memory_repo import MEMORY_MD_CHAR_LIMIT, MemoryRepo
from lite_horse.storage.db import db_session

log = logging.getLogger(__name__)

COMPACT_KIND = "compact"
DEFAULT_COMPACT_MODEL = MODEL_GPT_5_4_MINI
COMPACT_UTILIZATION_THRESHOLD = 0.8


@dataclass(frozen=True)
class CompactMessage:
    """One queued compaction: one ``memory.md`` for one (user, agent)."""

    kind: str
    user_id: str
    agent_id: str
    model: str

    @classmethod
    def new(
        cls,
        *,
        user_id: str,
        agent_id: str,
        model: str = "",
    ) -> CompactMessage:
        return cls(
            kind=COMPACT_KIND,
            user_id=user_id,
            agent_id=agent_id,
            model=model,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> CompactMessage:
        data = json.loads(raw)
        return cls(
            kind=str(data["kind"]),
            user_id=str(data["user_id"]),
            agent_id=str(data["agent_id"]),
            model=str(data.get("model") or ""),
        )


def is_compact_payload(raw: str) -> bool:
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("kind") == COMPACT_KIND


def _split_entries(body: str) -> list[str]:
    if not body.strip():
        return []
    delim = ENTRY_DELIMITER.strip()
    return [e.strip() for e in body.split(delim) if e.strip()]


def _join_entries(entries: list[str]) -> str:
    return ENTRY_DELIMITER.join(entries)


async def run_compact(
    message: CompactMessage,
    *,
    consolidator: Consolidator | None = None,
    embedder: EmbeddingProvider | None = None,
) -> bool:
    """Compact ``memory.md`` for one tenant in place.

    Returns ``True`` on success (caller deletes the SQS message) or on
    a benign no-op (memory already under threshold, side-agent returned
    no entries). ``False`` triggers SQS redelivery.
    """
    async with db_session(message.user_id, message.agent_id) as session:
        repo = MemoryRepo(session)
        body = await repo.get("memory.md")
        entries = _split_entries(body)

    if not entries:
        return True

    utilization = len(body) / MEMORY_MD_CHAR_LIMIT
    if utilization <= COMPACT_UTILIZATION_THRESHOLD:
        log.info(
            "compact: memory.md at %.2f utilisation, skipping (user=%s agent=%s)",
            utilization,
            message.user_id,
            message.agent_id,
        )
        return True

    model = message.model or DEFAULT_COMPACT_MODEL
    side_agent = consolidator or Consolidator(model=model)
    trajectory = [{"role": "memory", "content": e} for e in entries]
    try:
        new_entries = await side_agent.run(turn_input=trajectory)
    except Exception:
        log.exception(
            "compact: consolidator raised (user=%s agent=%s)",
            message.user_id,
            message.agent_id,
        )
        return False

    if not new_entries:
        log.info(
            "compact: consolidator returned no entries (user=%s agent=%s)",
            message.user_id,
            message.agent_id,
        )
        return True

    new_body = _join_entries(new_entries)
    if len(new_body) >= len(body):
        log.info(
            "compact: condensed body not shorter, skipping write (user=%s agent=%s)",
            message.user_id,
            message.agent_id,
        )
        return True

    async with db_session(message.user_id, message.agent_id) as session:
        await MemoryRepo(session).put("memory.md", new_body)

    recall = RecallCloudBackend(
        user_id=message.user_id,
        agent_id=message.agent_id,
        embedder=embedder or select_embedding_provider(),
    )
    for idx, entry in enumerate(new_entries):
        try:
            await recall.index(
                source_kind="memory_md",
                source_id=f"compact-{idx}",
                content=entry,
            )
        except Exception:
            log.exception(
                "compact: recall index failed (user=%s agent=%s idx=%d)",
                message.user_id,
                message.agent_id,
                idx,
            )
    log.info(
        "compact: memory.md %d -> %d chars (%d -> %d entries) user=%s agent=%s",
        len(body),
        len(new_body),
        len(entries),
        len(new_entries),
        message.user_id,
        message.agent_id,
    )
    return True
