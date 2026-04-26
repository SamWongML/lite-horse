"""LocalKms contract — mirror AWS Encryption SDK semantics with EncryptionContext binding."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from lite_horse.storage.kms import Kms, KmsDecryptError
from lite_horse.storage.kms_local import LocalKms


@pytest.fixture()
def kms() -> LocalKms:
    return LocalKms(Fernet.generate_key())


async def test_satisfies_protocol(kms: LocalKms) -> None:
    assert isinstance(kms, Kms)


async def test_roundtrip(kms: LocalKms) -> None:
    ctx = {"user_id": "00000000-0000-0000-0000-000000000001"}
    blob = await kms.encrypt(b"sk-secret", ctx)
    assert await kms.decrypt(blob, ctx) == b"sk-secret"


async def test_decrypt_fails_on_context_mismatch(kms: LocalKms) -> None:
    ctx_a = {"user_id": "user-a"}
    ctx_b = {"user_id": "user-b"}
    blob = await kms.encrypt(b"sk-secret", ctx_a)
    with pytest.raises(KmsDecryptError):
        await kms.decrypt(blob, ctx_b)


async def test_decrypt_fails_on_extra_context_field(kms: LocalKms) -> None:
    ctx_a = {"user_id": "u1"}
    ctx_b = {"user_id": "u1", "purpose": "byo-key"}
    blob = await kms.encrypt(b"sk-secret", ctx_a)
    with pytest.raises(KmsDecryptError):
        await kms.decrypt(blob, ctx_b)


async def test_encrypt_requires_user_id(kms: LocalKms) -> None:
    with pytest.raises(ValueError):
        await kms.encrypt(b"x", {"purpose": "byo-key"})


async def test_decrypt_requires_user_id(kms: LocalKms) -> None:
    blob = await kms.encrypt(b"x", {"user_id": "u1"})
    with pytest.raises(ValueError):
        await kms.decrypt(blob, {"purpose": "byo-key"})


async def test_decrypt_fails_on_truncated_blob(kms: LocalKms) -> None:
    with pytest.raises(KmsDecryptError):
        await kms.decrypt(b"\x00\x00", {"user_id": "u1"})


async def test_decrypt_fails_on_tampered_token(kms: LocalKms) -> None:
    blob = bytearray(await kms.encrypt(b"data", {"user_id": "u1"}))
    blob[-5] ^= 0xFF
    with pytest.raises(KmsDecryptError):
        await kms.decrypt(bytes(blob), {"user_id": "u1"})
