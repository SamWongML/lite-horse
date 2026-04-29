"""KMS-encrypted BYO provider keys — one JSON document per user.

The ``users.byo_provider_key_ct`` column holds the AWS-Encryption-SDK
ciphertext of a JSON document with shape::

    {
      "openai":   "<api_key>",
      "anthropic":"<api_key>",
      "github":   {"access_token":"…","refresh_token":"…","expires_at":…},
    }

Per the v0.4 Hard Contract every encrypt/decrypt MUST pin
``EncryptionContext={"user_id": <user_id>}`` so a stolen ciphertext
can't be decrypted under a different user's identity.

The repo's read path stays plaintext-quarantined: callers receive a
narrow accessor (``get_key("openai")``) instead of the full doc, so
plaintext leaks happen only at the explicit ``get_key`` call site.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from lite_horse.models.user import User
from lite_horse.repositories.base import BaseRepo
from lite_horse.storage.kms import Kms, KmsDecryptError

ProviderKeyName = str  # "openai" | "anthropic" | "github" — open-coded per shape


class ByoKeyStore(BaseRepo):
    """Per-user BYO provider keys, KMS-encrypted at rest."""

    def __init__(self, session: Any, kms: Kms) -> None:
        super().__init__(session)
        self._kms = kms

    async def _load_doc(self, user_id: str) -> dict[str, Any]:
        stmt = select(User.byo_provider_key_ct).where(User.id == UUID(user_id))
        row = (await self.session.execute(stmt)).first()
        if row is None or row[0] is None:
            return {}
        plaintext = await self._kms.decrypt(bytes(row[0]), {"user_id": user_id})
        try:
            doc = json.loads(plaintext.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise KmsDecryptError("byo_provider_key_ct is not valid JSON") from exc
        if not isinstance(doc, dict):
            raise KmsDecryptError("byo_provider_key_ct is not a JSON object")
        return doc

    async def _save_doc(self, user_id: str, doc: dict[str, Any]) -> None:
        if doc:
            blob = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
            ct = await self._kms.encrypt(blob, {"user_id": user_id})
        else:
            ct = None
        await self.session.execute(
            update(User)
            .where(User.id == UUID(user_id))
            .values(byo_provider_key_ct=ct)
        )

    async def get_key(self, provider: ProviderKeyName) -> str | None:
        """Return the API key for ``provider`` or ``None``.

        For ``github`` the returned string is the OAuth ``access_token``
        — same call shape as the bearer-token providers.
        """
        user_id = await self.current_user_id()
        doc = await self._load_doc(user_id)
        value = doc.get(provider)
        if value is None:
            return None
        if isinstance(value, dict):
            access = value.get("access_token")
            return str(access) if isinstance(access, str) else None
        return str(value)

    async def set_key(self, provider: ProviderKeyName, value: str) -> None:
        """Set ``provider`` to a bearer-token plaintext."""
        user_id = await self.current_user_id()
        doc = await self._load_doc(user_id)
        doc[provider] = value
        await self._save_doc(user_id, doc)

    async def set_github_oauth(
        self,
        *,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: int | None = None,
    ) -> None:
        """Persist a GitHub OAuth-app token bundle (Phase 37 §Open question 4)."""
        user_id = await self.current_user_id()
        doc = await self._load_doc(user_id)
        bundle: dict[str, Any] = {"access_token": access_token}
        if refresh_token is not None:
            bundle["refresh_token"] = refresh_token
        if expires_at is not None:
            bundle["expires_at"] = int(expires_at)
        doc["github"] = bundle
        await self._save_doc(user_id, doc)

    async def delete_key(self, provider: ProviderKeyName) -> bool:
        """Remove one provider's key. Returns True if a row was changed."""
        user_id = await self.current_user_id()
        doc = await self._load_doc(user_id)
        if provider not in doc:
            return False
        doc.pop(provider, None)
        await self._save_doc(user_id, doc)
        return True

    async def list_present(self) -> list[ProviderKeyName]:
        """Return the provider names with a non-null entry."""
        user_id = await self.current_user_id()
        doc = await self._load_doc(user_id)
        return sorted(doc.keys())
