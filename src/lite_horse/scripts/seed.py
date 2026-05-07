"""Seed bundled instructions/commands as ``official`` rows + a local admin user.

`make seed` invokes this once after `make migrate` so a fresh local
Postgres has:

* every bundled instruction promoted to ``scope='official'`` (idempotent
  — already-seeded slugs are skipped).
* every bundled command promoted to ``scope='official'`` (likewise).
* a single ``role='admin'`` user whose ``external_id`` matches
  ``LITEHORSE_LOCAL_ADMIN_SUB`` (default ``"local-admin"``) so the dev
  JWT minted by ``litehorse-webapp`` resolves to an admin without manual
  SQL. Already-existing rows are not overwritten.

Bundled skills are NOT auto-promoted: they're served from the bundled
scope at request time, and the admin promotes/edits select ones via the
admin API. Seeding them would create duplicate official rows that
shadow the bundled copy.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.bundled.loaders import (
    load_bundled_commands,
    load_bundled_instructions,
)
from lite_horse.models.user import User
from lite_horse.repositories.command_repo import CommandRepo
from lite_horse.repositories.instruction_repo import InstructionRepo
from lite_horse.storage.db import get_engine

_LOCAL_ADMIN_SUB_DEFAULT = "local-admin"


async def _seed_instructions(session: AsyncSession) -> int:
    repo = InstructionRepo(session)
    added = 0
    for item in load_bundled_instructions():
        existing = await repo.get_official(item.slug)
        if existing is not None:
            continue
        await repo.create_official(
            slug=item.slug,
            body=item.body,
            priority=item.priority,
            mandatory=item.mandatory,
        )
        added += 1
    return added


async def _seed_commands(session: AsyncSession) -> int:
    repo = CommandRepo(session)
    added = 0
    for item in load_bundled_commands():
        existing = await repo.get_official(item.slug)
        if existing is not None:
            continue
        await repo.create_official(
            slug=item.slug,
            prompt_tpl=item.prompt_tpl,
            description=item.description,
            arg_schema=item.arg_schema,
            bind_skills=item.bind_skills or None,
            mandatory=False,
        )
        added += 1
    return added


async def _seed_local_admin(session: AsyncSession) -> bool:
    sub = os.environ.get("LITEHORSE_LOCAL_ADMIN_SUB", _LOCAL_ADMIN_SUB_DEFAULT)
    existing = await session.execute(select(User.id).where(User.external_id == sub))
    if existing.scalar_one_or_none() is not None:
        return False
    stmt = (
        pg_insert(User)
        .values(id=uuid.uuid4(), external_id=sub, role="admin")
        .on_conflict_do_nothing(index_elements=[User.external_id])
    )
    await session.execute(stmt)
    return True


async def _seed() -> dict[str, int | bool]:
    engine = get_engine()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with session.begin():
            instructions = await _seed_instructions(session)
            commands = await _seed_commands(session)
            admin_created = await _seed_local_admin(session)
    return {
        "instructions_added": instructions,
        "commands_added": commands,
        "admin_created": admin_created,
    }


def main() -> int:
    summary = asyncio.run(_seed())
    print(
        "seed complete: "
        f"{summary['instructions_added']} instructions, "
        f"{summary['commands_added']} commands, "
        f"admin_created={summary['admin_created']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
