"""``ByoKeyStore``: KMS round-trip on the per-user provider-key document."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from lite_horse.repositories.byo_repo import ByoKeyStore
from lite_horse.storage.kms import KmsDecryptError
from lite_horse.storage.kms_local import LocalKms

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture()
def kms() -> LocalKms:
    return LocalKms(Fernet.generate_key())


async def test_set_then_get_round_trips(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    await store.set_key("openai", "sk-openai-test")
    assert await store.get_key("openai") == "sk-openai-test"


async def test_set_persists_ciphertext_only(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    await store.set_key("anthropic", "sk-ant-secret")
    row = (
        await pg_session.execute(
            text("SELECT byo_provider_key_ct FROM users LIMIT 1")
        )
    ).one()
    blob = bytes(row.byo_provider_key_ct)
    assert blob != b""
    assert b"sk-ant-secret" not in blob


async def test_get_unknown_provider_returns_none(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    assert await store.get_key("openai") is None


async def test_github_oauth_bundle_returns_access_token(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    await store.set_github_oauth(
        access_token="gho_abc",
        refresh_token="ghr_xyz",
        expires_at=1_700_000_000,
    )
    assert await store.get_key("github") == "gho_abc"
    present = await store.list_present()
    assert "github" in present


async def test_delete_key_removes_only_that_provider(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    await store.set_key("openai", "sk-1")
    await store.set_key("anthropic", "sk-2")
    removed = await store.delete_key("openai")
    assert removed is True
    assert await store.get_key("openai") is None
    assert await store.get_key("anthropic") == "sk-2"


async def test_delete_missing_key_returns_false(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    store = ByoKeyStore(pg_session, kms)
    assert await store.delete_key("openai") is False


async def test_encryption_context_pins_user_id(
    pg_session: AsyncSession, kms: LocalKms
) -> None:
    """A document encrypted for user A cannot be decrypted with user B's context."""
    store = ByoKeyStore(pg_session, kms)
    await store.set_key("openai", "sk-bound")
    blob = (
        await pg_session.execute(
            text("SELECT byo_provider_key_ct FROM users LIMIT 1")
        )
    ).scalar_one()
    # Same KMS, wrong context.
    with pytest.raises(KmsDecryptError):
        await kms.decrypt(bytes(blob), {"user_id": "00000000-0000-0000-0000-000000000000"})
