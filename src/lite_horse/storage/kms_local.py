"""Fernet-backed KMS — local dev + unit tests.

Wraps a Fernet token with a length-prefixed canonical-JSON encoding of
the encryption context, so that a tampered or mismatched context fails
decryption — matching the production AWS Encryption SDK semantics.

Wire format (big-endian):

    [4 bytes ctx_len][ctx_bytes utf-8 canonical JSON][fernet token]

The Fernet key authenticates the whole record (ctx is included in the
ciphertext input), so swapping the prefix breaks the MAC.
"""
from __future__ import annotations

import json
import struct

from cryptography.fernet import Fernet, InvalidToken

from lite_horse.storage.kms import Kms, KmsDecryptError


def _serialize_context(context: dict[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")


class LocalKms(Kms):
    def __init__(self, fernet_key: str | bytes) -> None:
        if isinstance(fernet_key, str):
            fernet_key = fernet_key.encode("ascii")
        self._fernet = Fernet(fernet_key)

    async def encrypt(self, plaintext: bytes, context: dict[str, str]) -> bytes:
        if "user_id" not in context:
            raise ValueError("EncryptionContext must include 'user_id'")
        ctx_bytes = _serialize_context(context)
        # Fernet authenticates the input; we feed it ctx || plaintext so
        # that a swapped prefix breaks decryption.
        token = self._fernet.encrypt(ctx_bytes + b"\x00" + plaintext)
        return struct.pack(">I", len(ctx_bytes)) + ctx_bytes + token

    async def decrypt(self, ciphertext: bytes, context: dict[str, str]) -> bytes:
        if "user_id" not in context:
            raise ValueError("EncryptionContext must include 'user_id'")
        try:
            (ctx_len,) = struct.unpack(">I", ciphertext[:4])
        except struct.error as exc:
            raise KmsDecryptError("ciphertext too short") from exc
        stored_ctx = ciphertext[4 : 4 + ctx_len]
        token = ciphertext[4 + ctx_len :]
        expected_ctx = _serialize_context(context)
        if stored_ctx != expected_ctx:
            raise KmsDecryptError("EncryptionContext mismatch")
        try:
            blob = self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise KmsDecryptError("fernet decryption failed") from exc
        sep = blob.find(b"\x00")
        if sep < 0 or blob[:sep] != stored_ctx:
            raise KmsDecryptError("inner context tampered")
        return blob[sep + 1 :]
