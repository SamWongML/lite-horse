"""Unit tests for :class:`Curator` helpers (Phase 44).

The full ``run_for_agent`` integration is exercised by the migration +
repository tests under ``tests/repositories``; here we pin the pure
methods (state transition, cosine pairing, reviewer parsing) so the
side-agent logic stays predictable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from lite_horse import curator as cur_mod
from lite_horse.constants import (
    CURATOR_ARCHIVE_AFTER_DAYS,
    CURATOR_STALE_AFTER_DAYS,
)


@dataclass
class _Skill:
    slug: str
    body: str = ""
    curator_state: str = "active"
    success_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime | None = None


def _now() -> datetime:
    return datetime(2026, 5, 13, tzinfo=UTC)


def test_target_state_active_recently_used() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(slug="x", last_used_at=_now() - timedelta(days=5))
    assert c._target_state(s, now=_now()) == "active"


def test_target_state_stale_after_30_days() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(
        slug="x",
        last_used_at=_now() - timedelta(days=CURATOR_STALE_AFTER_DAYS + 1),
        success_count=2,  # has successes — never archives
    )
    assert c._target_state(s, now=_now()) == "stale"


def test_target_state_archived_after_90_days_no_successes() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(
        slug="x",
        last_used_at=_now() - timedelta(days=CURATOR_ARCHIVE_AFTER_DAYS + 1),
        success_count=0,
    )
    assert c._target_state(s, now=_now()) == "archived"


def test_target_state_archived_threshold_with_successes_stays_stale() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(
        slug="x",
        last_used_at=_now() - timedelta(days=CURATOR_ARCHIVE_AFTER_DAYS + 1),
        success_count=4,
    )
    # ≥90 days idle but has successes — stays in 'stale'.
    assert c._target_state(s, now=_now()) == "stale"


def test_target_state_pinned_never_transitions() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(
        slug="x",
        curator_state="pinned",
        last_used_at=_now() - timedelta(days=365),
    )
    assert c._target_state(s, now=_now()) == "pinned"


def test_target_state_falls_back_to_created_at() -> None:
    c = cur_mod.Curator(model="test")
    s = _Skill(
        slug="x",
        last_used_at=None,
        created_at=_now() - timedelta(days=CURATOR_ARCHIVE_AFTER_DAYS + 1),
    )
    assert c._target_state(s, now=_now()) == "archived"


class _FixedEmbedder:
    name = "fixed"
    model = "test"
    dim = 3

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors

    async def embed(self, text: str) -> list[float]:
        return self._vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self._vectors[: len(texts)]


@pytest.mark.asyncio
async def test_find_consolidation_pairs_above_threshold() -> None:
    c = cur_mod.Curator(
        model="test",
        embedder=_FixedEmbedder(
            [
                [1.0, 0.0, 0.0],
                [0.99, 0.01, 0.0],
                [0.0, 1.0, 0.0],
            ]
        ),
        consolidate_threshold=0.85,
    )
    skills = [
        _Skill(slug="a", body="alpha"),
        _Skill(slug="b", body="alpha-similar"),
        _Skill(slug="c", body="totally-different"),
    ]
    pairs = await c._find_consolidation_pairs(skills)  # type: ignore[arg-type]
    assert len(pairs) == 1
    assert {pairs[0].a.slug, pairs[0].b.slug} == {"a", "b"}
    assert pairs[0].similarity >= 0.85


@pytest.mark.asyncio
async def test_find_consolidation_skips_with_null_embedder() -> None:
    # NullEmbeddingProvider is the default no-key fallback; the curator
    # should bail out cleanly rather than spinning up the reviewer agent.
    from lite_horse.providers.embedding import NullEmbeddingProvider

    c = cur_mod.Curator(model="test", embedder=NullEmbeddingProvider())
    pairs = await c._find_consolidation_pairs(  # type: ignore[arg-type]
        [_Skill(slug="a"), _Skill(slug="b")]
    )
    assert pairs == []


def test_cosine_handles_zero_vector() -> None:
    assert cur_mod._cosine([0, 0, 0], [1, 2, 3]) == 0.0
    assert cur_mod._cosine([], [1.0]) == 0.0
    assert cur_mod._cosine([1.0, 2.0, 3.0], [1.0, 2.0]) == 0.0


def test_parse_reviewer_merge_decision() -> None:
    decision = cur_mod._parse_reviewer(
        '{"merge": true, "slug": "a", "body": "merged", "reason": "ok"}'
    )
    assert decision is not None
    assert decision["merge"] is True
    assert decision["slug"] == "a"


def test_parse_reviewer_rejects_garbage() -> None:
    assert cur_mod._parse_reviewer("not json") is None
    assert cur_mod._parse_reviewer("[1, 2, 3]") is None


def test_parse_reviewer_strips_code_fence() -> None:
    decision = cur_mod._parse_reviewer(
        '```json\n{"merge": false, "reason": "different"}\n```'
    )
    assert decision is not None
    assert decision["merge"] is False
