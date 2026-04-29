"""Phase 38 observability — structured logs, OTel tracing, EMF metrics.

Three independent surfaces, each cheap to import and idempotent to call:

* :func:`configure_logging` — install ``structlog`` JSON renderer +
  ``contextvars`` merge so per-request fields (``request_id``,
  ``user_id``, ``session_key``, ``turn_id``) are auto-attached to every
  log line.
* :func:`configure_tracing` — boot the OTel SDK with an OTLP exporter
  and attach FastAPI / SQLAlchemy / httpx auto-instrumentations. No-op
  when no exporter endpoint is configured (local dev, unit tests).
* :func:`emit_metric` — write one CloudWatch EMF JSON line to stdout.
  No SDK round-trip; the CloudWatch agent on the ECS task picks it up.

The :class:`RequestIdMiddleware` / :class:`LoggingMiddleware` /
:class:`MetricsMiddleware` triplet wires the three surfaces into the
FastAPI app.
"""
from __future__ import annotations

from lite_horse.observability.logs import (
    bind_log_context,
    clear_log_context,
    configure_logging,
    get_logger,
)
from lite_horse.observability.metrics import emit_metric
from lite_horse.observability.middleware import (
    LoggingMiddleware,
    MetricsMiddleware,
    RequestIdMiddleware,
    install_middleware,
)
from lite_horse.observability.tracing import configure_tracing, get_tracer

__all__ = [
    "LoggingMiddleware",
    "MetricsMiddleware",
    "RequestIdMiddleware",
    "bind_log_context",
    "clear_log_context",
    "configure_logging",
    "configure_tracing",
    "emit_metric",
    "get_logger",
    "get_tracer",
    "install_middleware",
]
