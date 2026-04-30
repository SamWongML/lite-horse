"""Locust load profile — Phase 39 staging gate.

Run against staging:

    LITEHORSE_LOAD_BASE_URL=https://staging.lite-horse.example \\
    LITEHORSE_LOAD_BEARER=eyJ... \\
    locust -f tests/load/locustfile.py \\
           --headless -u 100 -r 10 -t 10m

Targets (from the v0.4 plan):

* 100 concurrent users x 10 turns/min x 10 min.
* p95 turn latency excluding LLM time < 500 ms.
* Zero failed requests at steady state.
* DB connections steady, no growth.

The script intentionally avoids any LLM-billed paths in the default
``test_plan`` — it hits the streaming endpoint with a no-op
``permission_mode='ro'`` payload so the staging deployment can run a
canned mock runner. Override ``LITEHORSE_LOAD_TURN_TEXT`` to send real
prompts when an end-to-end profile is desired.
"""
from __future__ import annotations

import os
import secrets
import uuid

try:
    from locust import HttpUser, between, task  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - locust is an optional dev dep
    HttpUser = object  # type: ignore[assignment,misc]
    between = lambda *_a, **_k: None  # type: ignore[assignment]  # noqa: E731

    def task(func):  # type: ignore[no-redef]
        return func


_DEFAULT_TURN_TEXT = "ping"


def _bearer() -> str:
    token = os.environ.get("LITEHORSE_LOAD_BEARER", "")
    if not token:
        raise RuntimeError(
            "LITEHORSE_LOAD_BEARER is required (mint a staging JWT first)"
        )
    return token


class LiteHorseUser(HttpUser):  # type: ignore[misc, valid-type]
    """One simulated user posting turns at the plan's cadence."""

    wait_time = between(5.5, 6.5)  # ~10 turns/min per user

    def on_start(self) -> None:
        self._idem = secrets.token_hex(8)
        self._session = f"load-{uuid.uuid4()}"
        self._headers = {
            "Authorization": f"Bearer {_bearer()}",
            "Content-Type": "application/json",
        }

    @task
    def post_turn(self) -> None:
        body = {
            "session_key": self._session,
            "text": os.environ.get("LITEHORSE_LOAD_TURN_TEXT", _DEFAULT_TURN_TEXT),
            "permission_mode": "ro",
        }
        headers = dict(self._headers)
        headers["Idempotency-Key"] = secrets.token_hex(12)
        with self.client.post(
            "/v1/turns",
            json=body,
            headers=headers,
            name="POST /v1/turns",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(
                    f"unexpected status {response.status_code}: {response.text[:200]}"
                )
            else:
                response.success()

    @task(2)
    def health(self) -> None:
        with self.client.get("/v1/ops/health", name="GET /v1/ops/health") as r:
            if r.status_code != 200:
                r.failure(f"health {r.status_code}")
