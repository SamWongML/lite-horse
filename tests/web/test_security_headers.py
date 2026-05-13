"""Phase 46 — security-headers middleware stamps the v0.5 hardening set."""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from lite_horse.web.middleware.security_headers import (
    SecurityHeadersMiddleware,
    install_security_headers,
)


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def test_security_headers_present_when_attached(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.headers["strict-transport-security"].startswith(
        "max-age=31536000"
    )
    assert "includeSubDomains" in resp.headers["strict-transport-security"]
    assert resp.headers["content-security-policy"] == "default-src 'none'"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


async def test_install_skips_local_env(app: FastAPI) -> None:
    install_security_headers(app, env="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert "strict-transport-security" not in resp.headers


async def test_install_attaches_for_cloud_env(app: FastAPI) -> None:
    install_security_headers(app, env="staging")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.get("/v1/health")
    assert "strict-transport-security" in resp.headers
    _ = os  # keep import line stable if test layout changes
