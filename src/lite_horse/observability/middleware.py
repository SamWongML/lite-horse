"""ASGI middleware that wires logs + metrics into every HTTP request.

Three small middlewares chained in this order on :func:`install_middleware`:

* :class:`RequestIdMiddleware` — pulls ``X-Request-Id`` (or generates
  one) and binds it on the structlog contextvars + on
  ``request.state.request_id``. Echoes it on the response.
* :class:`LoggingMiddleware` — emits one access log per request with
  method, path, status and ``latency_ms``.
* :class:`MetricsMiddleware` — emits an EMF ``http_requests_total``
  metric with method + status_class dimensions and a separate
  ``http_request_duration_ms`` metric.

The middlewares are independent: tests mount only the ones they need
and the app factory installs all three.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from lite_horse.observability.logs import (
    bind_log_context,
    clear_log_context,
    get_logger,
)
from lite_horse.observability.metrics import emit_metric

_REQUEST_ID_HEADER = "x-request-id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request id onto every incoming request."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(_REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        bind_log_context(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            clear_log_context()
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """One access log line per request, including ``latency_ms``."""

    def __init__(self, app: ASGIApp, *, logger_name: str = "lite_horse.access") -> None:
        super().__init__(app)
        self._logger = get_logger(logger_name)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
            self._logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=status,
                latency_ms=latency_ms,
            )


class MetricsMiddleware(BaseHTTPMiddleware):
    """Emit EMF metrics per request — count + latency."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
            dims = {
                "method": request.method,
                "status_class": f"{status // 100}xx",
            }
            emit_metric("http_requests_total", 1, dimensions=dims)
            emit_metric(
                "http_request_duration_ms",
                latency_ms,
                unit="Milliseconds",
                dimensions=dims,
            )


def install_middleware(app: FastAPI) -> None:
    """Attach the three observability middlewares to ``app`` in order.

    Starlette executes middleware in reverse-add order, so we add the
    inner-most layer (Metrics) first and the outer-most (RequestId) last.
    That way every log emitted from inner layers already has the
    request id bound.
    """
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)
