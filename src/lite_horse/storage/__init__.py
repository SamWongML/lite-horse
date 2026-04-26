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

__all__ = [
    "BlobStore",
    "Kms",
    "KmsDecryptError",
    "LockTimeout",
    "LockTimeoutError",
    "SecretsProvider",
    "SessionLock",
]
