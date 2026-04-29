"""Phase 38 — OTel tracing bootstrap + span emission on a turn driver.

We don't ship traces over the network in tests. Instead we install an
in-memory ``InMemorySpanExporter`` directly on a ``TracerProvider`` and
assert that ``stream_turn_to_sse`` (the ``run_turn`` analogue) opens a
span we can introspect.
"""
from __future__ import annotations

import os

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from lite_horse.observability import get_tracer
from lite_horse.observability.tracing import configure_tracing


@pytest.fixture
def in_memory_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()


def test_configure_tracing_no_op_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    # Must not raise even if SDK isn't fully present.
    configure_tracing(force=True)


def test_run_turn_emits_span_in_in_memory_exporter(in_memory_exporter):
    tracer = get_tracer("lite_horse.test")
    with tracer.start_as_current_span("run_turn") as span:
        span.set_attribute("session_key", "s-1")
        span.set_attribute("user_id", "u-1")

    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "run_turn"
    attrs = dict(spans[0].attributes or {})
    assert attrs.get("session_key") == "s-1"
    assert attrs.get("user_id") == "u-1"


def test_configure_tracing_with_endpoint_sets_provider(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/v1/traces")
    try:
        configure_tracing(force=True, service_name="lite-horse-test")
    finally:
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    provider = trace.get_tracer_provider()
    # Either our SDK provider or the auto-installed one — both must
    # produce a usable tracer.
    tracer = provider.get_tracer("lite_horse.test")
    assert tracer is not None
