"""SecretsProvider Protocol.

Cloud impl (`secrets_aws.py`) wraps `aws-secretsmanager-caching`. Local
impl (`secrets_env.py`) reads from the process environment.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsProvider(Protocol):
    """Resolve a secret by logical name."""

    async def get(self, name: str) -> str:
        ...
