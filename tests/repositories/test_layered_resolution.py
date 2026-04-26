"""Layered config resolution rules — bundled + official + user.

Phase 33b ships ``list_effective`` on each repo plus a top-level
``compute_effective_config`` that fans them out. The matrix below covers
every rule in the plan: mandatory officials, opt-outs, slug shadowing,
bundled inclusion, MCP-specific behaviours.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import (
    CommandRepo,
    EffectiveConfig,
    InstructionRepo,
    McpRepo,
    OptOutRepo,
    SkillRepo,
)
from lite_horse.storage.kms_local import LocalKms
from lite_horse.web.effective_config import compute_effective_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
def kms_for_test() -> LocalKms:
    return LocalKms(Fernet.generate_key())


# ---------- skills ----------


async def test_bundled_skills_always_present(pg_session: AsyncSession) -> None:
    """`plan` ships bundled — must surface even with empty DB."""
    resolved = await SkillRepo(pg_session).list_effective()
    slugs = {s.slug for s in resolved}
    assert "plan" in slugs
    assert all(s.scope == "bundled" for s in resolved if s.slug == "plan")


async def test_user_skill_shadows_non_mandatory_official(
    pg_session: AsyncSession,
) -> None:
    repo = SkillRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO skills
              (id, scope, user_id, slug, version, is_current, mandatory,
               enabled_default, frontmatter, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'pair', 1, true, false,
               true, '{"name":"pair"}'::jsonb, 'official body')
            """
        )
    )
    await repo.create_user(
        slug="pair", frontmatter={"name": "pair"}, body="user body"
    )
    resolved = {s.slug: s for s in await repo.list_effective()}
    assert resolved["pair"].scope == "user"
    assert resolved["pair"].body == "user body"


async def test_mandatory_official_skill_cannot_be_shadowed(
    pg_session: AsyncSession,
) -> None:
    repo = SkillRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO skills
              (id, scope, user_id, slug, version, is_current, mandatory,
               enabled_default, frontmatter, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'safety', 1, true, true,
               true, '{"name":"safety"}'::jsonb, 'mandatory body')
            """
        )
    )
    await repo.create_user(
        slug="safety", frontmatter={"name": "safety"}, body="evil body"
    )
    resolved = {s.slug: s for s in await repo.list_effective()}
    assert resolved["safety"].scope == "official"
    assert resolved["safety"].body == "mandatory body"


async def test_opt_out_drops_non_mandatory_official_skill(
    pg_session: AsyncSession,
) -> None:
    skill_repo = SkillRepo(pg_session)
    opt_out_repo = OptOutRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO skills
              (id, scope, user_id, slug, version, is_current, mandatory,
               enabled_default, frontmatter, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'noisy', 1, true, false,
               true, '{"name":"noisy"}'::jsonb, 'noisy body')
            """
        )
    )
    await opt_out_repo.add("skill", "noisy")
    out_slugs = {entity_slug for _, entity_slug in await opt_out_repo.list("skill")}
    resolved = await skill_repo.list_effective(out_slugs)
    assert "noisy" not in {s.slug for s in resolved}


async def test_opt_out_ignored_for_mandatory_official_skill(
    pg_session: AsyncSession,
) -> None:
    """Mandatory officials override the opt-out filter at the resolver level."""
    skill_repo = SkillRepo(pg_session)
    opt_out_repo = OptOutRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO skills
              (id, scope, user_id, slug, version, is_current, mandatory,
               enabled_default, frontmatter, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'must', 1, true, true,
               true, '{"name":"must"}'::jsonb, 'must body')
            """
        )
    )
    await opt_out_repo.add("skill", "must")
    out_slugs = {s for _, s in await opt_out_repo.list("skill")}
    slugs = {s.slug for s in await skill_repo.list_effective(out_slugs)}
    assert "must" in slugs


# ---------- instructions ----------


async def test_bundled_instructions_present_and_priority_ordered(
    pg_session: AsyncSession,
) -> None:
    """`safety-baseline` ships bundled at priority=10 → sorts ahead."""
    repo = InstructionRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO instructions
              (id, scope, user_id, slug, version, is_current, mandatory,
               priority, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'tone', 1, true, false,
               50, 'be concise')
            """
        )
    )
    resolved = await repo.list_effective()
    slugs_in_order = [i.slug for i in resolved]
    assert slugs_in_order.index("safety-baseline") < slugs_in_order.index("tone")


async def test_user_instruction_shadows_official(
    pg_session: AsyncSession,
) -> None:
    repo = InstructionRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO instructions
              (id, scope, user_id, slug, version, is_current, mandatory,
               priority, body)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'tone', 1, true, false,
               50, 'official tone')
            """
        )
    )
    await repo.create_user(slug="tone", body="user tone", priority=40)
    resolved = {i.slug: i for i in await repo.list_effective()}
    assert resolved["tone"].scope == "user"
    assert resolved["tone"].body == "user tone"


# ---------- commands ----------


async def test_bundled_command_resolves_and_expands(
    pg_session: AsyncSession,
) -> None:
    repo = CommandRepo(pg_session)
    resolved = {c.slug: c for c in await repo.list_effective()}
    assert "explain-stack-trace" in resolved
    assert resolved["explain-stack-trace"].scope == "bundled"
    out = await repo.expand(
        "explain-stack-trace", {"trace": "boom", "context": ""}
    )
    assert "boom" in out


async def test_user_command_shadows_official(pg_session: AsyncSession) -> None:
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
    resolved = {c.slug: c for c in await repo.list_effective()}
    assert resolved["greet"].scope == "user"
    assert resolved["greet"].prompt_tpl == "User: {{ name }}"


# ---------- mcp ----------


async def test_mcp_disabled_official_excluded(pg_session: AsyncSession) -> None:
    repo = McpRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO mcp_servers
              (id, scope, user_id, slug, url, cache_tools_list, enabled,
               mandatory, version, is_current)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'github',
               'https://example.com/mcp', true, false, false, 1, true)
            """
        )
    )
    resolved = await repo.list_effective()
    assert resolved == []


async def test_mcp_user_overrides_non_mandatory_official(
    pg_session: AsyncSession, kms_for_test
) -> None:
    repo = McpRepo(pg_session)
    await pg_session.execute(
        text(
            """
            INSERT INTO mcp_servers
              (id, scope, user_id, slug, url, cache_tools_list, enabled,
               mandatory, version, is_current)
            VALUES
              (gen_random_uuid(), 'official', NULL, 'github',
               'https://official.example.com', true, true, false, 1, true)
            """
        )
    )
    await repo.create_user(
        slug="github", url="https://user.example.com", kms=kms_for_test
    )
    resolved = {m.slug: m for m in await repo.list_effective()}
    assert resolved["github"].scope == "user"
    assert resolved["github"].url == "https://user.example.com"


# ---------- end-to-end resolver ----------


async def test_compute_effective_config_returns_etag(
    pg_session: AsyncSession,
) -> None:
    eff = await compute_effective_config(pg_session)
    assert isinstance(eff, EffectiveConfig)
    # `plan` skill + `safety-baseline` instruction + `explain-stack-trace`
    # command are all bundled — must survive an empty DB.
    assert any(s.slug == "plan" for s in eff.skills)
    assert any(i.slug == "safety-baseline" for i in eff.instructions)
    assert any(c.slug == "explain-stack-trace" for c in eff.commands)
    assert len(eff.etag) == 16  # truncated sha256 hex


async def test_etag_changes_when_user_skill_added(
    pg_session: AsyncSession,
) -> None:
    before = await compute_effective_config(pg_session)
    await SkillRepo(pg_session).create_user(
        slug="my-new-skill", frontmatter={"name": "my-new-skill"}, body="b"
    )
    after = await compute_effective_config(pg_session)
    assert before.etag != after.etag
