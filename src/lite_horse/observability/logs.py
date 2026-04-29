"""Structured logging — ``structlog`` configured for JSON-to-stdout.

Calling :func:`configure_logging` once at process boot rewires both
``structlog`` and the stdlib :mod:`logging` root so anything written
through either surface ends up as one JSON line per log call. The line
shape is locked to the v0.4 plan §Observability:

    {"ts","level","logger","event",
     "request_id?","user_id?","session_key?","turn_id?",
     "tool_name?","model?","latency_ms?",
     "tokens_in?","tokens_out?","error_kind?", ...}

Per-request fields are merged in via ``structlog.contextvars`` so a log
emitted from anywhere inside a request task picks them up automatically.

The local-dev flavour (``env="local"``) renders pretty console output
instead of JSON so a developer running ``uvicorn`` from the shell gets
something readable. Tests run with the JSON renderer to assert shape.
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_state: dict[str, bool] = {"configured": False}


def configure_logging(
    *,
    env: str = "prod",
    level: str = "INFO",
    force: bool = False,
) -> None:
    """Configure structlog + the stdlib root logger.

    Idempotent: a second call is a no-op unless ``force=True`` (used by
    tests that want to reset between cases).
    """
    if _state["configured"] and not force:
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if env == "local":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    else:
        renderer = structlog.processors.JSONRenderer(sort_keys=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge the stdlib root logger so libraries that use plain
    # ``logging.getLogger(__name__)`` (FastAPI, SQLAlchemy, openai, …)
    # land in the same JSON stream.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _StdlibStructlogFormatter(
            shared_processors=shared_processors, renderer=renderer
        )
    )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(log_level)

    # Quiet down a couple of noisy libraries by default. Apps can raise
    # them again after configure_logging() if they really want chatter.
    for noisy in ("uvicorn.access", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _state["configured"] = True


class _StdlibStructlogFormatter(logging.Formatter):
    """Format stdlib ``LogRecord`` instances through the structlog pipeline."""

    def __init__(
        self,
        *,
        shared_processors: list[Any],
        renderer: Any,
    ) -> None:
        super().__init__()
        self._wrapper = structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
            foreign_pre_chain=shared_processors,
        )

    def format(self, record: logging.LogRecord) -> str:
        return self._wrapper.format(record)


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger bound to ``name`` (or the caller's module)."""
    return structlog.get_logger(name) if name else structlog.get_logger()


def bind_log_context(**fields: Any) -> None:
    """Merge ``fields`` into the per-task contextvars log context."""
    structlog.contextvars.bind_contextvars(**fields)


def clear_log_context() -> None:
    """Drop every contextvars-bound log field for this task."""
    structlog.contextvars.clear_contextvars()
