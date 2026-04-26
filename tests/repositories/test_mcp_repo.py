"""mcp_servers repo: KMS round-trip on auth values."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories import McpRepo
from lite_horse.storage.kms import KmsDecryptError
from lite_horse.storage.kms_local import LocalKms

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
def kms() -> LocalKms:
    return LocalKms(Fernet.generate_key())


async def test_create_user_encrypts_auth_value(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    row = await repo.create_user(
        slug="github",
        url="https://api.example.com",
        kms=kms,
        auth_header="Authorization",
        auth_value="Bearer sk-secret",
    )
    # Ciphertext written, plaintext absent on the row object.
    assert row.auth_value_ct is not None
    assert b"sk-secret" not in bytes(row.auth_value_ct)
    decrypted = await repo.decrypt_auth_value(row, kms)
    assert decrypted == "Bearer sk-secret"


async def test_create_user_without_auth_value_leaves_columns_null(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    row = await repo.create_user(
        slug="public", url="https://public.example.com", kms=kms
    )
    assert row.auth_value_ct is None
    assert await repo.decrypt_auth_value(row, kms) is None


async def test_decrypt_fails_with_wrong_user_context(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    """A row encrypted for user A cannot be decrypted under user B's GUC."""
    repo = McpRepo(pg_session)
    row = await repo.create_user(
        slug="x", url="https://x", kms=kms, auth_value="secret"
    )
    # Manually fake a row whose owner has been rewritten.
    impostor = type(row)(
        id=row.id,
        scope=row.scope,
        user_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000bad"),
        slug=row.slug,
        url=row.url,
        auth_header=row.auth_header,
        auth_value_ct=row.auth_value_ct,
        auth_value_dk=None,
        cache_tools_list=row.cache_tools_list,
        enabled=row.enabled,
        mandatory=row.mandatory,
        version=row.version,
        is_current=row.is_current,
    )
    with pytest.raises(KmsDecryptError):
        await repo.decrypt_auth_value(impostor, kms)


async def test_update_user_replaces_auth_value(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    await repo.create_user(
        slug="x", url="https://x", kms=kms, auth_value="old"
    )
    updated = await repo.update_user(
        "x", kms=kms, auth_value="new", url="https://x2"
    )
    assert updated is not None
    assert updated.url == "https://x2"
    assert await repo.decrypt_auth_value(updated, kms) == "new"


async def test_update_user_clears_auth_value(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    await repo.create_user(
        slug="x", url="https://x", kms=kms, auth_value="secret"
    )
    updated = await repo.update_user("x", kms=kms, clear_auth_value=True)
    assert updated is not None
    assert updated.auth_value_ct is None


async def test_delete_user(pg_session: AsyncSession, kms: LocalKms) -> None:
    repo = McpRepo(pg_session)
    await repo.create_user(slug="x", url="https://x", kms=kms)
    assert await repo.delete_user("x") is True
    assert await repo.get_user("x") is None


async def test_list_user_orders_by_slug(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    for s in ("c", "a", "b"):
        await repo.create_user(slug=s, url=f"https://{s}", kms=kms)
    assert [r.slug for r in await repo.list_user()] == ["a", "b", "c"]


async def test_record_probe_stamps_meta(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    repo = McpRepo(pg_session)
    await repo.create_user(slug="x", url="https://x", kms=kms)
    when = datetime(2026, 4, 27, 12, 0, tzinfo=UTC)
    await repo.record_probe("x", ok=False, when=when)
    row = await repo.get_user("x")
    assert row is not None
    assert row.last_probe_ok is False
    assert row.last_probe_at == when
