"""Logging bootstrap for the ``litehorse`` CLI.

Installs two handlers on the root logger on first call:

1. A rotating file handler writing plain-text to
   ``~/.litehorse/litehorse.log`` so ``litehorse logs tail`` has data to
   show. Always attached; level defaults to ``INFO``.
2. A stderr handler that renders either as JSON lines (when structured
   mode is on) or via ``rich.logging.RichHandler`` (pretty, human-mode).

Structured mode activates when **any** of:

- ``LITEHORSE_STRUCTURED_LOGS`` is truthy in the environment, **or**
- ``--json`` is set on the current subcommand (caller passes it in).

The bootstrap is **idempotent**: repeated calls replace only the handlers
this module installed, so pytest runs that hit ``main()`` twice do not
accumulate handlers.

Kept deliberately cheap: no imports of ``rich`` at module top so the
``--help`` fast-path stays under budget. ``rich`` loads lazily only when
human-mode is picked.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

_HANDLER_ATTR = "_litehorse_installed"
_LOG_FILENAME = "litehorse.log"
_MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB — `logs tail` is interactive
_BACKUP_COUNT = 3

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


class JsonFormatter(logging.Formatter):
    """One-JSON-object-per-line formatter for ``LITEHORSE_STRUCTURED_LOGS``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), default=str)


def is_structured(json_mode: bool = False) -> bool:
    """True when structured (NDJSON) logging should be used."""
    return json_mode or _truthy(os.environ.get("LITEHORSE_STRUCTURED_LOGS"))


def log_file_path() -> Path:
    from lite_horse.cli._settings import state_dir

    return state_dir() / _LOG_FILENAME


def _remove_own_handlers(logger: logging.Logger) -> None:
    """Drop only handlers previously installed by this module."""
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_ATTR, False):
            logger.removeHandler(handler)


def _make_file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        path, maxBytes=_MAX_FILE_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    setattr(fh, _HANDLER_ATTR, True)
    return fh


def _make_stderr_handler(*, structured: bool) -> logging.Handler:
    if structured:
        sh: logging.Handler = logging.StreamHandler()
        sh.setFormatter(JsonFormatter())
    else:
        # Lazy import keeps the --help fast-path free of rich.
        from rich.console import Console
        from rich.logging import RichHandler

        sh = RichHandler(
            console=Console(stderr=True),
            show_time=True,
            show_path=False,
            markup=False,
            rich_tracebacks=True,
        )
    setattr(sh, _HANDLER_ATTR, True)
    return sh


def configure(
    *,
    json_mode: bool = False,
    debug: bool = False,
    log_path: Path | None = None,
) -> None:
    """Install CLI logging handlers on the root logger.

    Safe to call multiple times. Earlier handlers installed by this
    module are replaced; handlers installed by the host process are left
    alone.
    """
    root = logging.getLogger()
    _remove_own_handlers(root)

    level = logging.DEBUG if debug else logging.INFO
    root.setLevel(level)

    path = log_path or log_file_path()
    try:
        file_handler = _make_file_handler(path)
    except OSError:
        # Read-only home dirs etc. — skip file handler, keep stderr.
        file_handler = None
    if file_handler is not None:
        root.addHandler(file_handler)

    stderr_handler = _make_stderr_handler(structured=is_structured(json_mode))
    root.addHandler(stderr_handler)
