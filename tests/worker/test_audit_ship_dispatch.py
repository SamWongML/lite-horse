"""Phase 46 — worker dispatcher routes audit_ship payloads.

Pure dispatcher-shape tests; no Postgres / S3 needed.
"""
from __future__ import annotations

import json

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.audit_ship import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_RETENTION_DAYS,
    AuditShipMessage,
    is_audit_ship_payload,
)
from lite_horse.worker.runner import dispatch_message


def test_audit_ship_message_round_trips() -> None:
    msg = AuditShipMessage.new()
    raw = msg.to_json()
    assert is_audit_ship_payload(raw) is True
    back = AuditShipMessage.from_json(raw)
    assert back == msg
    assert back.retention_days == DEFAULT_RETENTION_DAYS
    assert back.limit == DEFAULT_BATCH_LIMIT


def test_is_audit_ship_payload_rejects_other_kinds() -> None:
    assert is_audit_ship_payload(json.dumps({"kind": "embed"})) is False
    assert is_audit_ship_payload(json.dumps({"kind": "gdpr_delete"})) is False
    assert is_audit_ship_payload("not json") is False


@pytest.mark.asyncio
async def test_dispatch_routes_to_audit_ship_handler() -> None:
    captured: list[AuditShipMessage] = []

    async def fake_ship(msg: AuditShipMessage) -> bool:
        captured.append(msg)
        return True

    raw = QueueMessage(
        body=AuditShipMessage.new(retention_days=120, limit=500).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, audit_ship_fn=fake_ship)
    assert ok is True
    assert len(captured) == 1
    assert captured[0].retention_days == 120
    assert captured[0].limit == 500


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_audit_ship_exception() -> None:
    async def boom(msg: AuditShipMessage) -> bool:
        raise RuntimeError("s3 down")

    raw = QueueMessage(
        body=AuditShipMessage.new().to_json(), receipt_handle="r-1"
    )
    ok = await dispatch_message(raw, audit_ship_fn=boom)
    assert ok is False
