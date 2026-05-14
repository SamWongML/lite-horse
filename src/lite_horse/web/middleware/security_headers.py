"""Security-headers middleware.

Stamps every response with the four headers the v0.5 hardening gate
requires:

* ``Strict-Transport-Security`` — one-year max-age, includeSubDomains,
  preload. The API is HTTPS-only behind the ALB; the header is a defence
  for browsers that hit the bare host directly.
* ``Content-Security-Policy: default-src 'none'`` — the API never serves
  HTML and never embeds scripts. The lockdown CSP keeps it that way.
* ``X-Frame-Options: DENY`` — no clickjacking.
* ``Referrer-Policy: no-referrer`` — outgoing fetches inherit the
  policy, so a leaked URL won't leak the originating path.

Local dev (``LITEHORSE_ENV=local``) skips the middleware so a developer
running ``uvicorn`` against ``http://localhost`` doesn't get HSTS
pinned for a year on their browser.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_HEADERS: dict[str, str] = {
    "Strict-Transport-Security": (
        "max-age=31536000; includeSubDomains; preload"
    ),
    "Content-Security-Policy": "default-src 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Stamp the v0.5 hardening headers on every response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def install_security_headers(app: FastAPI, *, env: str) -> None:
    """Attach :class:`SecurityHeadersMiddleware` unless ``env`` is local."""
    if env == "local":
        return
    app.add_middleware(SecurityHeadersMiddleware)


__all__ = ["SecurityHeadersMiddleware", "install_security_headers"]
