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
from lite_horse.storage.queue import MessageQueue, QueueMessage
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


_LOCAL_QUEUE_SINGLETON: MessageQueue | None = None


def make_secrets_provider() -> SecretsProvider:
    """Return the env-appropriate :class:`SecretsProvider` impl.

    Local: env-backed. Non-local: AWS Secrets Manager via cached client.
    """
    from lite_horse.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if settings.env == "local":
        from lite_horse.storage.secrets_env import (  # noqa: PLC0415
            EnvSecretsProvider,
        )

        return EnvSecretsProvider()

    from lite_horse.storage.secrets_aws import AwsSecretsProvider  # noqa: PLC0415

    return AwsSecretsProvider.from_settings()


def make_message_queue() -> MessageQueue:
    """Return the env-appropriate :class:`MessageQueue` impl.

    `LITEHORSE_ENV=local` → process-wide :class:`InMemoryMessageQueue`
    so a single Python process running both the scheduler tick and a
    worker drainer (handy in tests and `make dev`) shares one queue.
    Cross-process local setups must use SQS via LocalStack instead.
    Anything else → :class:`SqsMessageQueue` resolved from
    ``LITEHORSE_SQS_QUEUE_URL``. Imported lazily so the local path stays
    free of `aioboto3`.
    """
    global _LOCAL_QUEUE_SINGLETON  # noqa: PLW0603
    from lite_horse.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    if settings.env == "local":
        from lite_horse.storage.queue_memory import (  # noqa: PLC0415
            InMemoryMessageQueue,
        )

        if _LOCAL_QUEUE_SINGLETON is None:
            _LOCAL_QUEUE_SINGLETON = InMemoryMessageQueue()
        return _LOCAL_QUEUE_SINGLETON

    from lite_horse.storage.queue_sqs import SqsMessageQueue  # noqa: PLC0415

    return SqsMessageQueue.from_settings()


def reset_local_message_queue_for_tests() -> None:
    """Drop the local in-process queue singleton — tests only."""
    global _LOCAL_QUEUE_SINGLETON  # noqa: PLW0603
    _LOCAL_QUEUE_SINGLETON = None


__all__ = [
    "BlobStore",
    "Kms",
    "KmsDecryptError",
    "LockTimeout",
    "LockTimeoutError",
    "MessageQueue",
    "QueueMessage",
    "SecretsProvider",
    "SessionLock",
    "make_kms",
    "make_message_queue",
    "make_secrets_provider",
    "reset_local_message_queue_for_tests",
]
