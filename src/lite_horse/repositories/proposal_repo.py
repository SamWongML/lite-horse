"""Per-user repository over ``skill_proposals``.

Read path: list/get pending (and historical) proposals belonging to the
caller's tenant. Write path: ``approve(id)`` flips the row to
``approved`` and materialises a user-scope :class:`Skill` from the
proposal body; ``reject(id)`` flips the row to ``rejected``.

Approve is intentionally idempotent on the proposal status only — if a
user-scope skill with the same slug already exists, we surface a
``CONFLICT``-shaped exception (caller maps it to HTTP 409) so the user
chooses between updating the existing skill or rejecting the proposal.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select, update

from lite_horse.models.skill import Skill as SkillModel
from lite_horse.models.skill_proposal import SkillProposal
from lite_horse.repositories.base import BaseRepo
from lite_horse.repositories.skill_repo import SkillRepo

VALID_STATUSES = ("pending", "approved", "rejected", "superseded")


class ProposalNotFoundError(Exception):
    pass


class ProposalAlreadyDecidedError(Exception):
    pass


class ProposalSkillSlugTakenError(Exception):
    """User already has a user-scope skill with this slug."""


def _row_to_dict(p: SkillProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "skill_slug": p.skill_slug,
        "base_version": p.base_version,
        "body": p.body,
        "fitness": dict(p.fitness) if p.fitness is not None else None,
        "status": p.status,
        "created_at": p.created_at,
        "decided_at": p.decided_at,
    }


class ProposalRepo(BaseRepo):
    """``skill_proposals`` CRUD scoped to the current ``app.user_id``."""

    async def list_(
        self, *, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        user_id = UUID(await self.current_user_id())
        stmt = select(SkillProposal).where(SkillProposal.user_id == user_id)
        if status is not None:
            if status not in VALID_STATUSES:
                raise ValueError(f"unknown proposal status: {status!r}")
            stmt = stmt.where(SkillProposal.status == status)
        stmt = stmt.order_by(SkillProposal.created_at.desc()).limit(int(limit))
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_row_to_dict(r) for r in rows]

    async def get(self, proposal_id: str) -> dict[str, Any] | None:
        user_id = UUID(await self.current_user_id())
        stmt = select(SkillProposal).where(
            and_(
                SkillProposal.user_id == user_id,
                SkillProposal.id == UUID(proposal_id),
            )
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        return _row_to_dict(row) if row is not None else None

    async def approve(self, proposal_id: str) -> dict[str, Any]:
        """Flip to approved and create a user-scope :class:`Skill`.

        Returns the updated proposal row dict. Raises:
          * :class:`ProposalNotFoundError` if no row matches the tenant.
          * :class:`ProposalAlreadyDecidedError` if status != ``pending``.
          * :class:`ProposalSkillSlugTakenError` if a user-scope skill with the
            proposal's ``skill_slug`` already exists.
        """
        user_id = UUID(await self.current_user_id())
        stmt = select(SkillProposal).where(
            and_(
                SkillProposal.user_id == user_id,
                SkillProposal.id == UUID(proposal_id),
            )
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ProposalNotFoundError(proposal_id)
        if row.status != "pending":
            raise ProposalAlreadyDecidedError(row.status)

        skill_repo = SkillRepo(self.session)
        existing = await skill_repo.get_user(row.skill_slug)
        if existing is not None:
            raise ProposalSkillSlugTakenError(row.skill_slug)

        await skill_repo.create_user(
            slug=row.skill_slug,
            frontmatter={"slug": row.skill_slug, "source": "evolve"},
            body=row.body,
            enabled_default=True,
        )
        now = datetime.now(UTC)
        await self.session.execute(
            update(SkillProposal)
            .where(SkillProposal.id == row.id)
            .values(status="approved", decided_at=now)
        )
        return {**_row_to_dict(row), "status": "approved", "decided_at": now}

    async def reject(self, proposal_id: str) -> dict[str, Any]:
        user_id = UUID(await self.current_user_id())
        stmt = select(SkillProposal).where(
            and_(
                SkillProposal.user_id == user_id,
                SkillProposal.id == UUID(proposal_id),
            )
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise ProposalNotFoundError(proposal_id)
        if row.status != "pending":
            raise ProposalAlreadyDecidedError(row.status)
        now = datetime.now(UTC)
        await self.session.execute(
            update(SkillProposal)
            .where(SkillProposal.id == row.id)
            .values(status="rejected", decided_at=now)
        )
        return {**_row_to_dict(row), "status": "rejected", "decided_at": now}


# `SkillModel` re-export keeps `from lite_horse.repositories.proposal_repo
# import SkillModel` callable for tests that want to assert the new row
# was inserted; otherwise unused.
__all__ = [
    "VALID_STATUSES",
    "ProposalAlreadyDecidedError",
    "ProposalNotFoundError",
    "ProposalRepo",
    "ProposalSkillSlugTakenError",
    "SkillModel",
]
