"""SQS-backed MessageQueue via `aioboto3`.

A new client is acquired per call (cheap with `aioboto3.Session`,
botocore pools connections under the hood). MaxNumberOfMessages caps at
10 per the SQS API; ``WaitTimeSeconds`` enables long-polling.
"""
from __future__ import annotations

from typing import Any

import aioboto3

from lite_horse.config import get_settings
from lite_horse.storage.queue import MessageQueue, QueueMessage


class SqsMessageQueue(MessageQueue):
    def __init__(
        self,
        queue_url: str,
        endpoint_url: str | None = None,
        region_name: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        self._queue_url = queue_url
        self._endpoint = endpoint_url
        self._region = region_name
        self._access_key = access_key
        self._secret_key = secret_key
        self._session = aioboto3.Session()

    @classmethod
    def from_settings(cls) -> SqsMessageQueue:
        s = get_settings()
        if not s.sqs_queue_url:
            raise RuntimeError("LITEHORSE_SQS_QUEUE_URL is not configured")
        return cls(
            queue_url=s.sqs_queue_url,
            endpoint_url=s.sqs_endpoint,
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

    async def send(self, body: str) -> None:
        async with self._session.client("sqs", **self._client_kwargs()) as client:
            await client.send_message(QueueUrl=self._queue_url, MessageBody=body)

    async def receive(
        self, *, max_messages: int = 10, wait_seconds: int = 20
    ) -> list[QueueMessage]:
        max_n = min(10, max(1, int(max_messages)))
        wait = min(20, max(0, int(wait_seconds)))
        async with self._session.client("sqs", **self._client_kwargs()) as client:
            resp = await client.receive_message(
                QueueUrl=self._queue_url,
                MaxNumberOfMessages=max_n,
                WaitTimeSeconds=wait,
            )
        out: list[QueueMessage] = []
        for raw in resp.get("Messages", []) or []:
            out.append(
                QueueMessage(
                    body=str(raw.get("Body", "")),
                    receipt_handle=str(raw.get("ReceiptHandle", "")),
                )
            )
        return out

    async def delete(self, receipt_handle: str) -> None:
        async with self._session.client("sqs", **self._client_kwargs()) as client:
            await client.delete_message(
                QueueUrl=self._queue_url, ReceiptHandle=receipt_handle
            )
