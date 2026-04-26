"""S3-backed BlobStore via `aioboto3`.

Honours `LITEHORSE_S3_ENDPOINT` for MinIO compatibility. A new client is
acquired per call (cheap for `aioboto3.Session` — connections are pooled
under the hood by `botocore`); revisit if profiling says otherwise.
"""
from __future__ import annotations

from typing import Any

import aioboto3
from botocore.exceptions import ClientError

from lite_horse.config import get_settings
from lite_horse.storage.blob import BlobStore


class S3BlobStore(BlobStore):
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        self._bucket = bucket
        self._endpoint = endpoint_url
        self._region = region_name
        self._access_key = access_key
        self._secret_key = secret_key
        self._session = aioboto3.Session()

    @classmethod
    def from_settings(cls, bucket: str) -> S3BlobStore:
        s = get_settings()
        return cls(
            bucket=bucket,
            endpoint_url=s.s3_endpoint,
            region_name=s.s3_region,
            access_key=s.s3_access_key,
            secret_key=s.s3_secret_key,
        )

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"region_name": self._region}
        if self._endpoint:
            kwargs["endpoint_url"] = self._endpoint
        if self._access_key is not None:
            kwargs["aws_access_key_id"] = self._access_key
        if self._secret_key is not None:
            kwargs["aws_secret_access_key"] = self._secret_key
        return kwargs

    async def put(
        self,
        key: str,
        body: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        async with self._session.client("s3", **self._client_kwargs()) as client:
            await client.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType=content_type
            )

    async def get(self, key: str) -> bytes:
        async with self._session.client("s3", **self._client_kwargs()) as client:
            try:
                resp = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code in ("NoSuchKey", "404"):
                    raise FileNotFoundError(key) from exc
                raise
            async with resp["Body"] as stream:
                payload: bytes = await stream.read()
                return payload

    async def delete(self, key: str) -> None:
        async with self._session.client("s3", **self._client_kwargs()) as client:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def presign_get(self, key: str, expires: int = 900) -> str:
        async with self._session.client("s3", **self._client_kwargs()) as client:
            url: str = await client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires,
            )
            return url
