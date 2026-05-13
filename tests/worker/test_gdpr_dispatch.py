"""Phase 46 — worker dispatcher routes gdpr_delete payloads."""
from __future__ import annotations

import json
import uuid

import pytest

from lite_horse.storage.queue import QueueMessage
from lite_horse.worker.gdpr import GdprDeleteMessage, is_gdpr_delete_payload
from lite_horse.worker.runner import dispatch_message


def test_gdpr_message_round_trips() -> None:
    rid = str(uuid.uuid4())
    msg = GdprDeleteMessage.new(request_id=rid)
    raw = msg.to_json()
    assert is_gdpr_delete_payload(raw) is True
    back = GdprDeleteMessage.from_json(raw)
    assert back == msg
    assert back.request_id == rid


def test_is_gdpr_payload_rejects_other_kinds() -> None:
    assert is_gdpr_delete_payload(json.dumps({"kind": "audit_ship"})) is False
    assert is_gdpr_delete_payload(json.dumps({"kind": "embed"})) is False
    assert is_gdpr_delete_payload("not json") is False


@pytest.mark.asyncio
async def test_dispatch_routes_to_gdpr_handler() -> None:
    captured: list[GdprDeleteMessage] = []

    async def fake_gdpr(msg: GdprDeleteMessage) -> bool:
        captured.append(msg)
        return True

    rid = str(uuid.uuid4())
    raw = QueueMessage(
        body=GdprDeleteMessage.new(request_id=rid).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, gdpr_delete_fn=fake_gdpr)
    assert ok is True
    assert len(captured) == 1
    assert captured[0].request_id == rid


@pytest.mark.asyncio
async def test_dispatch_returns_false_on_gdpr_exception() -> None:
    async def boom(msg: GdprDeleteMessage) -> bool:
        raise RuntimeError("postgres down")

    raw = QueueMessage(
        body=GdprDeleteMessage.new(request_id=str(uuid.uuid4())).to_json(),
        receipt_handle="r-1",
    )
    ok = await dispatch_message(raw, gdpr_delete_fn=boom)
    assert ok is False
