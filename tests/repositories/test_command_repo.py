"""commands repo: user-scope CRUD + sandboxed Jinja expand()."""
from __future__ import annotations

import pytest
from jinja2 import UndefinedError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import CommandRepo

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_create_then_expand_user(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    await repo.create_user(
        slug="greet",
        prompt_tpl="Hello, {{ name }}!",
        description="say hi",
    )
    out = await repo.expand("greet", {"name": "Sen"})
    assert out == "Hello, Sen!"


async def test_expand_user_shadows_official(pg_session: AsyncSession) -> None:
    """User-scope row wins over official row with the same slug."""
    repo = CommandRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO commands
              (id, scope, user_id, slug, version, is_current, mandatory,
               prompt_tpl)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'greet', 1, true, false,
               'Official: {{ name }}')
            """
        )
    )
    await repo.create_user(slug="greet", prompt_tpl="User: {{ name }}")
    assert await repo.expand("greet", {"name": "S"}) == "User: S"


async def test_expand_falls_back_to_official(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO commands
              (id, scope, user_id, slug, version, is_current, mandatory,
               prompt_tpl)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'help', 1, true, false,
               'Help with {{ topic }}')
            """
        )
    )
    out = await repo.expand("help", {"topic": "stack traces"})
    assert out == "Help with stack traces"


async def test_expand_unknown_slug_raises(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    with pytest.raises(KeyError, match="unknown command slug"):
        await repo.expand("nope", {})


async def test_expand_strict_undefined_raises(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    await repo.create_user(slug="x", prompt_tpl="{{ missing }}")
    with pytest.raises(UndefinedError):
        await repo.expand("x", {})


async def test_expand_blocks_attribute_traversal(
    pg_session: AsyncSession,
) -> None:
    """SandboxedEnvironment forbids access to ``__class__`` etc."""
    from jinja2.exceptions import SecurityError

    repo = CommandRepo(pg_session)
    await repo.create_user(
        slug="evil",
        prompt_tpl="{{ obj.__class__.__mro__[1].__subclasses__() }}",
    )
    with pytest.raises(SecurityError):
        await repo.expand("evil", {"obj": object()})


async def test_update_user_changes_template(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    await repo.create_user(slug="x", prompt_tpl="v1: {{ a }}")
    await repo.update_user("x", prompt_tpl="v2: {{ a }}")
    assert await repo.expand("x", {"a": "Q"}) == "v2: Q"


async def test_list_user_orders_by_slug(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    for s in ("c", "a", "b"):
        await repo.create_user(slug=s, prompt_tpl=s)
    assert [r.slug for r in await repo.list_user()] == ["a", "b", "c"]


async def test_delete_user(pg_session: AsyncSession) -> None:
    repo = CommandRepo(pg_session)
    await repo.create_user(slug="x", prompt_tpl="x")
    assert await repo.delete_user("x") is True
    assert await repo.get_user("x") is None


async def test_create_duplicate_slug_per_user_rejected(
    pg_session: AsyncSession,
) -> None:
    """Partial unique index `(user_id, slug) WHERE is_current` must fire."""
    repo = CommandRepo(pg_session)
    await repo.create_user(slug="dup", prompt_tpl="a")
    with pytest.raises(IntegrityError):
        await repo.create_user(slug="dup", prompt_tpl="b")
