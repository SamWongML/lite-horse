"""Structured error classification (Phase 22).

``except Exception: log.exception(...)`` loses the signal — rate limits look
the same as tool bugs as context overflows. Callers that want to retry, bail,
or stamp a strike need a *kind*. This module dispatches raw exceptions from
the ``openai`` + ``agents`` SDKs to one of a handful of :class:`ErrorKind`
values and reports whether the caller should retry.

Feeds:
- ``lite_horse.api.run_turn`` — retries RATE_LIMIT / NETWORK, raises the rest.
- ``lite_horse.cron.scheduler`` — counts MODEL_REFUSAL strikes per job.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

import openai
from agents.exceptions import (
    MCPToolCancellationError,
    ModelBehaviorError,
    ToolTimeoutError,
)

_SUMMARY_CHARS = 200

_CONTEXT_OVERFLOW_CODES: frozenset[str] = frozenset(
    {"context_length_exceeded", "string_above_max_length"}
)
_CONTEXT_OVERFLOW_MARKERS: tuple[str, ...] = (
    "context length",
    "context_length",
    "maximum context",
    "too long for",
)


class ErrorKind(enum.StrEnum):
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    MODEL_REFUSAL = "model_refusal"
    TOOL_ERROR = "tool_error"
    NETWORK = "network"
    UNKNOWN = "unknown"


_RETRYABLE: frozenset[ErrorKind] = frozenset(
    {ErrorKind.RATE_LIMIT, ErrorKind.NETWORK}
)


@dataclass(frozen=True)
class ClassifiedError:
    kind: ErrorKind
    retryable: bool
    original: BaseException
    summary: str


def classify(exc: BaseException) -> ClassifiedError:
    """Dispatch ``exc`` to an :class:`ErrorKind`.

    ``BadRequestError`` is inspected first for the context-overflow signal;
    all other dispatch is a flat ``isinstance`` table walked in order so a
    subclass always wins over its base.
    """
    if isinstance(exc, openai.BadRequestError):
        kind = (
            ErrorKind.CONTEXT_OVERFLOW
            if _is_context_overflow(exc)
            else ErrorKind.UNKNOWN
        )
        return _make(kind, exc)
    for exc_types, kind in _DISPATCH:
        if isinstance(exc, exc_types):
            return _make(kind, exc)
    return _make(ErrorKind.UNKNOWN, exc)


_DISPATCH: tuple[tuple[tuple[type[BaseException], ...], ErrorKind], ...] = (
    ((openai.RateLimitError,), ErrorKind.RATE_LIMIT),
    # APIConnectionError covers APITimeoutError; InternalServerError is 5xx.
    (
        (openai.APIConnectionError, openai.InternalServerError),
        ErrorKind.NETWORK,
    ),
    (
        (openai.ContentFilterFinishReasonError, ModelBehaviorError),
        ErrorKind.MODEL_REFUSAL,
    ),
    ((ToolTimeoutError, MCPToolCancellationError), ErrorKind.TOOL_ERROR),
)


def _is_context_overflow(exc: openai.BadRequestError) -> bool:
    code = (getattr(exc, "code", None) or "").lower()
    if code in _CONTEXT_OVERFLOW_CODES:
        return True
    message = (getattr(exc, "message", None) or str(exc)).lower()
    return any(marker in message for marker in _CONTEXT_OVERFLOW_MARKERS)


def _make(kind: ErrorKind, exc: BaseException) -> ClassifiedError:
    return ClassifiedError(
        kind=kind,
        retryable=kind in _RETRYABLE,
        original=exc,
        summary=_summarize(exc),
    )


def _summarize(exc: BaseException) -> str:
    text = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    return str(text)[:_SUMMARY_CHARS]
