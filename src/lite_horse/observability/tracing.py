"""OpenTelemetry SDK bootstrap + auto-instrumentation.

Production ECS tasks ship traces to an ADOT sidecar over OTLP/HTTP and
the sidecar forwards to AWS X-Ray. Locally and in CI we leave the
exporter off so tests don't try to dial out — :func:`configure_tracing`
silently no-ops when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset.

The function is idempotent. Auto-instrumentation hooks for FastAPI,
SQLAlchemy and httpx are installed once on first call; later calls fall
through.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_log = logging.getLogger(__name__)
_state: dict[str, bool] = {"configured": False}


def configure_tracing(
    *,
    service_name: str = "lite-horse",
    env: str = "prod",
    force: bool = False,
) -> None:
    """Initialise the OTel SDK once per process.

    No-ops when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset. Tests rely on
    that behaviour to avoid network IO.
    """
    if _state["configured"] and not force:
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        _log.debug("tracing: OTEL_EXPORTER_OTLP_ENDPOINT unset — tracing disabled")
        _state["configured"] = True
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "deployment.environment": env,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    _install_auto_instrumentation()
    _state["configured"] = True
    _log.info("tracing: configured exporter=%s service=%s", endpoint, service_name)


def _install_auto_instrumentation() -> None:
    """Best-effort auto-instrument of FastAPI / SQLAlchemy / httpx."""
    for name, fn in (
        ("FastAPI", FastAPIInstrumentor().instrument),
        ("SQLAlchemy", SQLAlchemyInstrumentor().instrument),
        ("httpx", HTTPXClientInstrumentor().instrument),
    ):
        try:
            fn()
        except Exception as exc:  # pragma: no cover - opt-in instrumentation
            _log.debug("tracing: %s instrumentation skipped (%s)", name, exc)


def get_tracer(name: str = "lite_horse") -> Any:
    """Return an OTel tracer."""
    return trace.get_tracer(name)
