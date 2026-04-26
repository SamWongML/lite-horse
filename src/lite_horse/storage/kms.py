"""KMS Protocol — envelope encryption with EncryptionContext binding.

Cloud impl (`kms_aws.py`) uses the AWS Encryption SDK with one app KMS
key. Local impl (`kms_local.py`) uses a single Fernet key with a binding
check that mirrors the production EncryptionContext semantics.

Per the v0.4 Hard Contract: encryption context MUST contain `user_id`,
and decryption with a mismatched context MUST fail fast.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


class KmsDecryptError(Exception):
    """Raised when decryption fails (bad ciphertext, wrong context, expired key)."""


@runtime_checkable
class Kms(Protocol):
    async def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes:
        ...

    async def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes:
        ...
