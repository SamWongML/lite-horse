"""skills repo: user-scope CRUD round-trip + scope-agnostic read."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import SkillRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_then_get_user(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    row = await repo.create_user(
        slug="my-skill",
        frontmatter={"name": "my-skill", "description": "do the thing"},
        body="# my-skill\n\nbody",
    )
    assert row.scope == "user"
    assert row.version == 1
    assert row.is_current is True
    fetched = await repo.get_user("my-skill")
    assert fetched is not None
    assert fetched.slug == "my-skill"
    assert fetched.frontmatter["name"] == "my-skill"


async def test_get_user_missing_returns_none(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    assert await repo.get_user("nope") is None


async def test_list_user_orders_by_slug(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    for slug in ("zeta", "alpha", "mu"):
        await repo.create_user(slug=slug, frontmatter={"name": slug}, body=slug)
    assert [s.slug for s in await repo.list_user()] == ["alpha", "mu", "zeta"]


async def test_update_user_in_place(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    await repo.create_user(
        slug="s1", frontmatter={"name": "s1"}, body="v1"
    )
    updated = await repo.update_user("s1", body="v2", enabled_default=False)
    assert updated is not None
    assert updated.body == "v2"
    assert updated.enabled_default is False
    # No version bump for user scope.
    assert updated.version == 1


async def test_update_user_missing_returns_none(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    assert await repo.update_user("ghost", body="x") is None


async def test_update_user_no_fields_returns_current_row(
    pg_session: AsyncSession,
) -> None:
    repo = SkillRepo(pg_session)
    await repo.create_user(slug="s1", frontmatter={"name": "s1"}, body="b")
    row = await repo.update_user("s1")
    assert row is not None
    assert row.slug == "s1"


async def test_delete_user(pg_session: AsyncSession) -> None:
    repo = SkillRepo(pg_session)
    await repo.create_user(slug="s1", frontmatter={"name": "s1"}, body="b")
    assert await repo.delete_user("s1") is True
    assert await repo.delete_user("s1") is False
    assert await repo.get_user("s1") is None


async def test_list_official_sees_seeded_official_rows(
    pg_session: AsyncSession,
) -> None:
    """User-scope list never returns officials, even with same slug."""
    repo = SkillRepo(pg_session)
    # Seed an official row directly (Phase 34 owns the admin write path;
    # here we just verify read-side scope filtering).
    await pg_session.execute(
        text(
            """
            INSERT INTO skills
              (id, scope, user_id, slug, version, is_current, mandatory,
               enabled_default, frontmatter, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'shared', 1, true, false,
               true, '{"name":"shared"}'::jsonb, 'official body')
            """
        )
    )
    await repo.create_user(
        slug="shared",
        frontmatter={"name": "shared"},
        body="user body",
    )
    user_rows = await repo.list_user()
    official_rows = await repo.list_official()
    assert [r.body for r in user_rows] == ["user body"]
    assert "official body" in [r.body for r in official_rows]


async def test_user_scope_is_isolated_per_user(
    pg_session: AsyncSession,
) -> None:
    """Switching the GUC hides the previous user's rows."""
    repo = SkillRepo(pg_session)
    await repo.create_user(
        slug="mine", frontmatter={"name": "mine"}, body="b"
    )
    other_user_id = "00000000-0000-0000-0000-000000000bad"
    await pg_session.execute(text("INSERT INTO users (id, external_id) VALUES (:i, :e)"),
                              {"i": other_user_id, "e": "other"})
    await pg_session.execute(
        text("SELECT set_config('app.user_id', :u, true)"),
        {"u": other_user_id},
    )
    assert await repo.list_user() == []
