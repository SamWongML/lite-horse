"""JWT verification + lazy user provisioning.

Per the v0.4 Hard Contract (Phase 31):

* JWT signed by the webapp, verified locally against the issuer's JWKS.
* JWKS is fetched once per process and refreshed every hour.
* On first sight of an `external_id`, a row is created in `users` with
  the role from the JWT claim. The runtime `RequestContext.role` is
  always taken from the JWT, never re-read from the DB.

The `_get_jwks` and `_lookup_or_create_user` helpers are module-level so
unit tests can monkeypatch them without standing up Postgres or a JWKS
server.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
from fastapi import Request
from jose import jwt
from jose.exceptions import JOSEError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lite_horse.config import get_settings
from lite_horse.models.user import User
from lite_horse.observability import bind_log_context
from lite_horse.storage.db import db_session
from lite_horse.web.context import RequestContext
from lite_horse.web.errors import ErrorKind, http_error

_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL_SECONDS: float = 3600.0
_BEARER_PREFIX = "bearer "


def _extract_bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith(_BEARER_PREFIX):
        raise http_error(ErrorKind.UNAUTHORIZED, "missing bearer token")
    return header[len(_BEARER_PREFIX) :].strip()


async def _get_jwks(url: str) -> dict[str, Any]:
    """Return the JWKS document for `url`, cached for an hour per URL."""
    now = time.monotonic()
    cached = _JWKS_CACHE.get(url)
    if cached is not None and now - cached[0] < _JWKS_TTL_SECONDS:
        return cached[1]
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        jwks: dict[str, Any] = resp.json()
    _JWKS_CACHE[url] = (now, jwks)
    return jwks


def _clear_jwks_cache() -> None:
    """Test hook — flush the in-process JWKS cache."""
    _JWKS_CACHE.clear()


async def verify_jwt(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        jwks = await _get_jwks(settings.jwt_jwks_url)
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        keys = jwks.get("keys", [])
        key = next((k for k in keys if k.get("kid") == kid), None)
        if key is None:
            raise http_error(ErrorKind.UNAUTHORIZED, "JWT signing key not found")
        algorithm = key.get("alg") or header.get("alg") or "RS256"
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JOSEError as exc:
        raise http_error(ErrorKind.UNAUTHORIZED, f"invalid JWT: {exc}") from exc
    except httpx.HTTPError as exc:
        raise http_error(ErrorKind.UNAVAILABLE, f"JWKS fetch failed: {exc}") from exc
    return claims


async def _lookup_or_create_user(external_id: str, role: str) -> str:
    """Return the internal user UUID, creating the row on first sight.

    Race-safe: relies on Postgres `ON CONFLICT (external_id) DO NOTHING`
    plus a follow-up `SELECT` if the insert collided.
    """
    async with db_session(user_id=None) as session:
        stmt = (
            pg_insert(User)
            .values(id=uuid.uuid4(), external_id=external_id, role=role)
            .on_conflict_do_nothing(index_elements=[User.external_id])
            .returning(User.id)
        )
        result = await session.execute(stmt)
        new_id = result.scalar_one_or_none()
        if new_id is not None:
            return str(new_id)
        existing = await session.execute(
            select(User.id).where(User.external_id == external_id)
        )
        return str(existing.scalar_one())


async def authenticate_request(request: Request) -> RequestContext:
    token = _extract_bearer_token(request)
    claims = await verify_jwt(token)
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise http_error(ErrorKind.UNAUTHORIZED, "missing sub claim")
    role_claim = claims.get("role", "user")
    role = role_claim if role_claim in ("user", "admin") else "user"
    user_id = await _lookup_or_create_user(sub, role)
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("x-request-id")
        or str(uuid.uuid4())
    )
    bind_log_context(user_id=user_id, request_id=request_id)
    return RequestContext(
        user_id=user_id, external_id=sub, role=role, request_id=request_id
    )
