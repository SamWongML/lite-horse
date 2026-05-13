"""``skill_promotion_candidates`` repository — Phase 45.

Admin-only surface: callers are either the daily ``promotion_tick`` (no
``app.user_id`` GUC) or the admin HTTP routes (gated by ``role=admin``
JWT). There is no per-tenant filter — every method operates across all
users. RLS is **not** enabled on this table; the admin role does not
set ``app.user_id`` so RLS would silently swallow every row.

Three responsibilities:

* :meth:`aggregate_candidates` — cross-tenant scan of user-scope skills
  joined with their counter columns. The daily tick passes the result to
  :meth:`upsert_pending` to materialise one pending row per
  ``frontmatter.name``.
* :meth:`upsert_pending` — idempotent write keyed on
  ``frontmatter_name`` (partial unique index in the migration).
* :meth:`list_pending` / :meth:`get` / :meth:`mark_promoted` /
  :meth:`mark_rejected` — the admin endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.models.skill import Skill
from lite_horse.models.skill_promotion import SkillPromotionCandidate
from lite_horse.repositories.base import BaseRepo


@dataclass(frozen=True)
class CandidateAggregate:
    """One coalesced row from the cross-tenant skill scan."""

    frontmatter_name: str
    source_skill_id: str
    unique_user_count: int
    use_count: int
    success_count: int
    error_count: int

    @property
    def success_rate(self) -> float:
        denom = self.success_count + self.error_count
        if denom == 0:
            return 0.0
        return self.success_count / denom


class SkillPromotionRepo(BaseRepo):
    """``skill_promotion_candidates`` admin-only CRUD."""

    # ---------- cross-tenant aggregation ----------

    async def aggregate_candidates(
        self,
        *,
        min_unique_users: int,
        min_use_count: int,
        min_success_rate: float,
    ) -> list[CandidateAggregate]:
        """Scan user-scope skills, group by ``frontmatter.name``.

        Skills without a ``name`` in frontmatter are skipped. The
        returned list only contains rows that pass every threshold.
        """
        name_expr = Skill.frontmatter["name"].astext.label("fname")
        stmt = (
            select(
                name_expr,
                func.min(Skill.id).label("source_skill_id"),
                func.count(func.distinct(Skill.user_id)).label("uniq_users"),
                func.coalesce(func.sum(Skill.use_count), 0).label("uses"),
                func.coalesce(func.sum(Skill.success_count), 0).label("succ"),
                func.coalesce(func.sum(Skill.error_count), 0).label("errs"),
            )
            .where(
                and_(
                    Skill.scope == "user",
                    Skill.is_current.is_(True),
                    Skill.frontmatter["name"].astext.is_not(None),
                )
            )
            .group_by(name_expr)
        )
        rows = (await self.session.execute(stmt)).all()
        out: list[CandidateAggregate] = []
        for r in rows:
            agg = CandidateAggregate(
                frontmatter_name=str(r.fname),
                source_skill_id=str(r.source_skill_id),
                unique_user_count=int(r.uniq_users or 0),
                use_count=int(r.uses or 0),
                success_count=int(r.succ or 0),
                error_count=int(r.errs or 0),
            )
            if agg.unique_user_count < min_unique_users:
                continue
            if agg.use_count < min_use_count:
                continue
            if agg.success_rate < min_success_rate:
                continue
            out.append(agg)
        return out

    # ---------- writes ----------

    async def upsert_pending(self, agg: CandidateAggregate) -> UUID:
        """Insert a pending candidate, or refresh stats if one already exists.

        Idempotent on ``frontmatter_name`` via the partial unique index
        ``skill_promotion_candidates_pending_name``.
        """
        new_id = uuid4()
        stmt = (
            pg_insert(SkillPromotionCandidate)
            .values(
                id=new_id,
                source_skill_id=UUID(agg.source_skill_id),
                frontmatter_name=agg.frontmatter_name,
                unique_user_count=agg.unique_user_count,
                use_count=agg.use_count,
                success_rate=agg.success_rate,
                status="pending",
            )
            .on_conflict_do_update(
                index_elements=["frontmatter_name"],
                index_where=SkillPromotionCandidate.status == "pending",
                set_={
                    "source_skill_id": UUID(agg.source_skill_id),
                    "unique_user_count": agg.unique_user_count,
                    "use_count": agg.use_count,
                    "success_rate": agg.success_rate,
                    "generated_at": func.now(),
                },
            )
            .returning(SkillPromotionCandidate.id)
        )
        result = await self.session.execute(stmt)
        return UUID(str(result.scalar_one()))

    async def mark_promoted(
        self,
        candidate_id: UUID,
        *,
        admin_user_id: UUID,
        promoted_skill_id: UUID,
    ) -> SkillPromotionCandidate | None:
        stmt = (
            update(SkillPromotionCandidate)
            .where(
                and_(
                    SkillPromotionCandidate.id == candidate_id,
                    SkillPromotionCandidate.status == "pending",
                )
            )
            .values(
                status="promoted",
                decided_by=admin_user_id,
                promoted_skill_id=promoted_skill_id,
                decided_at=datetime.now(UTC),
            )
            .returning(SkillPromotionCandidate)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_rejected(
        self,
        candidate_id: UUID,
        *,
        admin_user_id: UUID,
        reason: str | None = None,
    ) -> SkillPromotionCandidate | None:
        stmt = (
            update(SkillPromotionCandidate)
            .where(
                and_(
                    SkillPromotionCandidate.id == candidate_id,
                    SkillPromotionCandidate.status == "pending",
                )
            )
            .values(
                status="rejected",
                decided_by=admin_user_id,
                reason=reason,
                decided_at=datetime.now(UTC),
            )
            .returning(SkillPromotionCandidate)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ---------- reads ----------

    async def get(self, candidate_id: UUID) -> SkillPromotionCandidate | None:
        stmt = select(SkillPromotionCandidate).where(
            SkillPromotionCandidate.id == candidate_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_pending(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[SkillPromotionCandidate]:
        stmt = (
            select(SkillPromotionCandidate)
            .where(SkillPromotionCandidate.status == "pending")
            .order_by(SkillPromotionCandidate.generated_at.desc())
            .limit(int(limit))
            .offset(int(offset))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_all(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SkillPromotionCandidate]:
        stmt = select(SkillPromotionCandidate)
        if status is not None:
            stmt = stmt.where(SkillPromotionCandidate.status == status)
        stmt = (
            stmt.order_by(SkillPromotionCandidate.generated_at.desc())
            .limit(int(limit))
            .offset(int(offset))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_source_skill(
        self, source_skill_id: UUID
    ) -> Skill | None:
        """Read the user-scope skill row that seeded a candidate.

        Used by the promote endpoint to clone the body / frontmatter
        into the ``official`` scope. Runs in an admin transaction
        (no ``app.user_id`` GUC) — RLS on ``skills`` blocks reads when
        ``app.user_id`` is unset, so this method **must** be called
        from a session opened by an admin path (the route handler
        opens its own admin-scope ``db_session``).
        """
        stmt = select(Skill).where(Skill.id == source_skill_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "CandidateAggregate",
    "SkillPromotionRepo",
]


# Mypy / re-export courtesy
_ = Any
