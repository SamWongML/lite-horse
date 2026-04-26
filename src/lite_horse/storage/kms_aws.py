"""AWS-KMS-backed `Kms` via the AWS Encryption SDK with DataKey caching.

* Single app KMS key (alias or ARN) configured by `LITEHORSE_AWS_KMS_KEY_ID`.
* `LocalCryptoMaterialsCache` (1000 entries) + per-cache TTL of 5 min keeps
  KMS API calls bounded under burst load.
* `EncryptionContext` MUST contain `user_id`; on decrypt we re-validate
  every caller-supplied key/value against the recovered header.

The Encryption SDK is sync; we wrap the encrypt/decrypt calls in
`asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio

import aws_encryption_sdk
from aws_encryption_sdk import CommitmentPolicy
from aws_encryption_sdk.caches.local import LocalCryptoMaterialsCache
from aws_encryption_sdk.exceptions import AWSEncryptionSDKClientError
from aws_encryption_sdk.key_providers.kms import StrictAwsKmsMasterKeyProvider
from aws_encryption_sdk.materials_managers.caching import CachingCryptoMaterialsManager

from lite_horse.config import get_settings
from lite_horse.storage.kms import Kms, KmsDecryptError

_DEFAULT_MAX_AGE_SECONDS = 300
_DEFAULT_MAX_MESSAGES = 10_000
_DEFAULT_CACHE_CAPACITY = 1000


class AwsKms(Kms):
    def __init__(
        self,
        key_id: str,
        max_age_seconds: int = _DEFAULT_MAX_AGE_SECONDS,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        cache_capacity: int = _DEFAULT_CACHE_CAPACITY,
    ) -> None:
        self._client = aws_encryption_sdk.EncryptionSDKClient(
            commitment_policy=CommitmentPolicy.REQUIRE_ENCRYPT_REQUIRE_DECRYPT,
        )
        provider = StrictAwsKmsMasterKeyProvider(key_ids=[key_id])
        cache = LocalCryptoMaterialsCache(capacity=cache_capacity)
        self._cmm = CachingCryptoMaterialsManager(
            master_key_provider=provider,
            cache=cache,
            max_age=max_age_seconds,
            max_messages_encrypted=max_messages,
        )

    @classmethod
    def from_settings(cls) -> AwsKms:
        key_id = get_settings().aws_kms_key_id
        if not key_id:
            raise RuntimeError("LITEHORSE_AWS_KMS_KEY_ID is not configured")
        return cls(key_id=key_id)

    async def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes:
        if "user_id" not in context:
            raise ValueError("EncryptionContext must include 'user_id'")

        def _do() -> bytes:
            ciphertext, _header = self._client.encrypt(
                source=plaintext,
                materials_manager=self._cmm,
                encryption_context=context,
            )
            assert isinstance(ciphertext, bytes)
            return ciphertext

        return await asyncio.to_thread(_do)

    async def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes:
        if "user_id" not in context:
            raise ValueError("EncryptionContext must include 'user_id'")

        def _do() -> bytes:
            try:
                plaintext, header = self._client.decrypt(
                    source=ciphertext, materials_manager=self._cmm
                )
            except AWSEncryptionSDKClientError as exc:
                raise KmsDecryptError(str(exc)) from exc
            for k, v in context.items():
                if header.encryption_context.get(k) != v:
                    raise KmsDecryptError(f"EncryptionContext mismatch on {k!r}")
            assert isinstance(plaintext, bytes)
            return plaintext

        return await asyncio.to_thread(_do)
