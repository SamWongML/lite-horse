"""Liveness + readiness probes — unit-level (DB and Redis stubbed)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from lite_horse.web import create_app
from lite_horse.web.routes import ops as ops_module


@pytest.fixture
def app():
    return create_app()


async def test_health_returns_ok(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_ready_returns_ok_when_deps_healthy(app):
    async def _ok() -> bool:
        return True

    app.dependency_overrides[ops_module.check_db] = _ok
    app.dependency_overrides[ops_module.check_redis] = _ok
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/v1/ready")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


async def test_ready_returns_503_when_db_down(app):
    async def _ok() -> bool:
        return True

    async def _db_fail() -> bool:
        from lite_horse.web.errors import ErrorKind, http_error

        raise http_error(ErrorKind.UNAVAILABLE, "db simulated down")

    app.dependency_overrides[ops_module.check_db] = _db_fail
    app.dependency_overrides[ops_module.check_redis] = _ok
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/v1/ready")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 503
    assert resp.json()["detail"]["kind"] == "UNAVAILABLE"
