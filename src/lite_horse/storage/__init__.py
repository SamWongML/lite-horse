"""Storage abstraction layer.

Per the v0.4 Hard Contract, this is the only package allowed to import
`boto3` / `aiobotocore` / `redis` / `aws_encryption_sdk`. All cloud
primitives are accessed by the rest of the codebase through Protocols
defined here, with at least two implementations each (production +
local). Selection is by `LITEHORSE_ENV` via `get_settings()`.
"""

from lite_horse.storage.blob import BlobStore
from lite_horse.storage.kms import Kms, KmsDecryptError
from lite_horse.storage.locks import LockTimeout, LockTimeoutError, SessionLock
from lite_horse.storage.secrets import SecretsProvider


def make_kms() -> Kms:
    """Return the env-appropriate Kms impl.

    `LITEHORSE_ENV=local` → Fernet-backed `LocalKms` (key from
    ``LITEHORSE_LOCAL_KMS_KEY``). Anything else → `AwsKms` resolved against
    ``LITEHORSE_AWS_KMS_KEY_ID``. Imported lazily to keep the AWS Encryption
    SDK out of the local-dev import path.
    """
    from lite_horse.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if settings.env == "local":
        from lite_horse.storage.kms_local import LocalKms  # noqa: PLC0415

        if not settings.local_kms_key:
            raise RuntimeError("LITEHORSE_LOCAL_KMS_KEY is not configured")
        return LocalKms(settings.local_kms_key)

    from lite_horse.storage.kms_aws import AwsKms  # noqa: PLC0415

    return AwsKms.from_settings()


__all__ = [
    "BlobStore",
    "Kms",
    "KmsDecryptError",
    "LockTimeout",
    "LockTimeoutError",
    "SecretsProvider",
    "SessionLock",
    "make_kms",
]
