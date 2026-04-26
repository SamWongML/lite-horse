"""AWS Secrets Manager-backed `SecretsProvider` with caching.

`aws-secretsmanager-caching` is sync; we wrap reads in `asyncio.to_thread`
to keep the async surface clean.
"""
from __future__ import annotations

import asyncio

import boto3
from aws_secretsmanager_caching import SecretCache, SecretCacheConfig

from lite_horse.config import get_settings
from lite_horse.storage.secrets import SecretsProvider


class AwsSecretsProvider(SecretsProvider):
    def __init__(self, region: str = "us-east-1", ttl_seconds: int = 300) -> None:
        client = boto3.client("secretsmanager", region_name=region)
        self._cache = SecretCache(
            config=SecretCacheConfig(secret_refresh_interval=ttl_seconds),
            client=client,
        )

    @classmethod
    def from_settings(cls) -> AwsSecretsProvider:
        return cls(region=get_settings().s3_region)

    async def get(self, name: str) -> str:
        value: str = await asyncio.to_thread(self._cache.get_secret_string, name)
        return value
