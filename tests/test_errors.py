"""Tests for the structured error classifier (Phase 22)."""
from __future__ import annotations

import dataclasses

import openai
import pytest
from agents.exceptions import (
    MCPToolCancellationError,
    ModelBehaviorError,
    ToolTimeoutError,
)
from httpx import Request, Response

from lite_horse.agent.errors import ClassifiedError, ErrorKind, classify


def _resp(status: int) -> Response:
    return Response(status, request=Request("POST", "https://api.openai.com/v1/x"))


# ---------- each ErrorKind dispatched ----------


def test_classify_rate_limit() -> None:
    err = openai.RateLimitError("slow down", response=_resp(429), body=None)
    c = classify(err)
    assert c.kind is ErrorKind.RATE_LIMIT
    assert c.retryable is True
    assert c.original is err
    assert "slow down" in c.summary


def test_classify_network_connection() -> None:
    req = Request("POST", "https://api.openai.com/v1/x")
    err = openai.APIConnectionError(request=req)
    c = classify(err)
    assert c.kind is ErrorKind.NETWORK
    assert c.retryable is True


def test_classify_network_timeout() -> None:
    req = Request("POST", "https://api.openai.com/v1/x")
    err = openai.APITimeoutError(request=req)
    c = classify(err)
    assert c.kind is ErrorKind.NETWORK
    assert c.retryable is True


def test_classify_network_5xx() -> None:
    err = openai.InternalServerError(
        "upstream boom", response=_resp(503), body=None
    )
    c = classify(err)
    assert c.kind is ErrorKind.NETWORK
    assert c.retryable is True


def test_classify_context_overflow_via_code() -> None:
    err = openai.BadRequestError(
        "too many tokens",
        response=_resp(400),
        body={"code": "context_length_exceeded", "message": "too many"},
    )
    c = classify(err)
    assert c.kind is ErrorKind.CONTEXT_OVERFLOW
    assert c.retryable is False


def test_classify_context_overflow_via_message() -> None:
    err = openai.BadRequestError(
        "This model's maximum context length is 128000 tokens.",
        response=_resp(400),
        body=None,
    )
    c = classify(err)
    assert c.kind is ErrorKind.CONTEXT_OVERFLOW


def test_classify_context_overflow_string_above_max_length() -> None:
    err = openai.BadRequestError(
        "too long",
        response=_resp(400),
        body={"code": "string_above_max_length"},
    )
    c = classify(err)
    assert c.kind is ErrorKind.CONTEXT_OVERFLOW


def test_classify_model_refusal_content_filter() -> None:
    err = openai.ContentFilterFinishReasonError()
    c = classify(err)
    assert c.kind is ErrorKind.MODEL_REFUSAL
    assert c.retryable is False


def test_classify_model_refusal_sdk_behavior() -> None:
    err = ModelBehaviorError("the model misbehaved")
    c = classify(err)
    assert c.kind is ErrorKind.MODEL_REFUSAL
    assert c.retryable is False
    assert "misbehaved" in c.summary


def test_classify_tool_error_timeout() -> None:
    err = ToolTimeoutError("tool hung", timeout_seconds=30.0)
    c = classify(err)
    assert c.kind is ErrorKind.TOOL_ERROR
    assert c.retryable is False


def test_classify_tool_error_mcp_cancellation() -> None:
    err = MCPToolCancellationError("cancelled")
    c = classify(err)
    assert c.kind is ErrorKind.TOOL_ERROR
    assert c.retryable is False


# ---------- non-retryable fallthroughs stay non-retryable ----------


def test_classify_bad_request_without_overflow_is_unknown() -> None:
    err = openai.BadRequestError(
        "invalid tool schema",
        response=_resp(400),
        body={"code": "invalid_request_error"},
    )
    c = classify(err)
    assert c.kind is ErrorKind.UNKNOWN
    assert c.retryable is False


def test_classify_authentication_is_unknown_not_retryable() -> None:
    err = openai.AuthenticationError(
        "bad key", response=_resp(401), body=None
    )
    c = classify(err)
    assert c.kind is ErrorKind.UNKNOWN
    assert c.retryable is False


def test_classify_plain_exception_is_unknown() -> None:
    c = classify(RuntimeError("surprise"))
    assert c.kind is ErrorKind.UNKNOWN
    assert c.retryable is False
    assert c.summary == "surprise"


# ---------- total: every ErrorKind is reachable via at least one exception ----------


def test_classifier_covers_every_error_kind() -> None:
    seen: set[ErrorKind] = set()
    samples: list[BaseException] = [
        openai.RateLimitError("x", response=_resp(429), body=None),
        openai.APIConnectionError(request=Request("POST", "x")),
        openai.BadRequestError(
            "context length exceeded", response=_resp(400), body=None
        ),
        openai.ContentFilterFinishReasonError(),
        ToolTimeoutError("x", timeout_seconds=1.0),
        RuntimeError("x"),
    ]
    for exc in samples:
        seen.add(classify(exc).kind)
    assert seen == set(ErrorKind)


def test_classified_error_is_frozen() -> None:
    c = classify(RuntimeError("x"))
    assert isinstance(c, ClassifiedError)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.kind = ErrorKind.RATE_LIMIT  # type: ignore[misc]
