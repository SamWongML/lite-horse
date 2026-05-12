"""Smoke test for ``POST /v1/turns/{turn_id}/feedback`` (Phase 44).

Heavy plumbing (Redis, JWT, DB) is stubbed via dependency overrides so
the test only exercises the route's request-validation + response shape.
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from lite_horse.web import create_app
from lite_horse.web.deps import get_request_context

pytestmark = pytest.mark.asyncio


def _override_ctx(app: Any, user_id: str = "11111111-1111-1111-1111-111111111111") -> None:
    from lite_horse.web.context import RequestContext

    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user_id=user_id, request_id="r1", external_id="ext-1", role="user"
    )


async def test_feedback_rating_invalid_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    _override_ctx(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/v1/turns/22222222-2222-2222-2222-222222222222/feedback",
            json={"session_key": "s1", "rating": 7},
        )
    assert resp.status_code == 422


async def test_feedback_records_user_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lite_horse.agent.backends.feedback import OutcomeRecord
    from lite_horse.web.routes import turns as turns_route

    captured: dict[str, Any] = {}

    async def _fake_resolve_agent_limits(*, user_id: str, requested_agent_id: str | None):
        captured["user_id"] = user_id
        return turns_route._AgentLimits(
            agent_id="33333333-3333-3333-3333-333333333333",
            rate_limit_per_min=None,
            cost_budget_usd_micro=None,
            user_settings=None,  # type: ignore[arg-type]
        )

    class _StubBackend:
        def __init__(self, *, user_id: str, agent_id: str) -> None:
            captured["agent_id"] = agent_id

        async def record(
            self,
            *,
            session_id: str,
            turn_id: str,
            source: str,
            rating: int,
            reason: str | None = None,
            skill_slug: str | None = None,
        ) -> OutcomeRecord:
            captured["call"] = {
                "session_id": session_id,
                "turn_id": turn_id,
                "source": source,
                "rating": rating,
                "reason": reason,
                "skill_slug": skill_slug,
            }
            return OutcomeRecord(
                session_id=session_id,
                turn_id=turn_id,
                source=source,  # type: ignore[arg-type]
                rating=rating,
                reason=reason,
                skill_slug=skill_slug,
                ts_iso="2026-05-13T00:00:00Z",
            )

    monkeypatch.setattr(
        turns_route, "_resolve_agent_limits", _fake_resolve_agent_limits
    )
    monkeypatch.setattr(turns_route, "FeedbackCloudBackend", _StubBackend)

    app = create_app()
    _override_ctx(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/v1/turns/22222222-2222-2222-2222-222222222222/feedback",
            json={
                "session_key": "s1",
                "rating": 1,
                "reason": "great",
                "skill_slug": "writeup",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rating"] == 1
    assert body["source"] == "user_explicit"
    assert captured["call"]["session_id"] == "s1"
    assert captured["call"]["skill_slug"] == "writeup"
    assert captured["agent_id"] == "33333333-3333-3333-3333-333333333333"


async def test_feedback_in_rejects_oversize_reason() -> None:
    from lite_horse.web.routes.turns import FeedbackIn

    with pytest.raises(ValidationError):
        FeedbackIn(session_key="s", rating=1, reason="x" * 500)
