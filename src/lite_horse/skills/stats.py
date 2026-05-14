"""Per-skill usage statistics sidecar.

The API is path-based: callers pass the skill directory (``Path``)
explicitly so this module stays free of any ``skills_root()`` or
``litehorse_home()`` access. The local skill backend resolves the path;
the cloud backend skips this module entirely (counters live on the
``skills`` table there).

Each skill owns a ``.stats.json`` sidecar inside its skill directory. The
file is touched on every successful ``skill_view`` (``record_view``) and
on run end when the :class:`EvolutionHook` observes a viewed skill in the
trajectory (``record_outcome``). The numbers feed two downstream
consumers:

- ``instructions._skills_index`` appends a *fragile* decay tag when a
  skill's success ratio drops below 50 % with at least 3 recorded errors.
- The offline evolve pipeline uses the counts to prioritise which
  skills to mutate.

Writes are guarded by an ``fcntl`` advisory lock so the hook and the view
tool cannot clobber each other. We target POSIX (linux for the embed);
``fcntl`` is unavailable on Windows and the lock becomes a no-op there.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_STATS_FILENAME = ".stats.json"
_ERROR_SUMMARY_MAX_CHARS = 500

try:  # POSIX has fcntl; Windows does not. No-op on Windows.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - covered by the posix path in tests
    _fcntl = None  # type: ignore[assignment]


def _default_stats() -> dict[str, Any]:
    return {
        "usage_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_used_at": None,
        "last_error_at": None,
        "last_error_summary": None,
        "last_optimized_at": None,
        "schema_version": SCHEMA_VERSION,
    }


def _sidecar(skill_dir: Path) -> Path:
    return skill_dir / _STATS_FILENAME


@contextlib.contextmanager
def _locked(path: Path) -> Any:
    """Open ``path`` for r+ with an exclusive advisory lock; create if missing.

    The lock is released on context exit regardless of exceptions. On
    non-POSIX platforms the lock degrades to a plain file handle.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        yield fd
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_fd(fd: int) -> dict[str, Any]:
    os.lseek(fd, 0, os.SEEK_SET)
    raw = b""
    while True:
        chunk = os.read(fd, 8192)
        if not chunk:
            break
        raw += chunk
    if not raw:
        return _default_stats()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _default_stats()
    if not isinstance(data, dict):
        return _default_stats()
    merged = _default_stats()
    merged.update({k: v for k, v in data.items() if k in merged})
    return merged


def _write_fd(fd: int, data: dict[str, Any]) -> None:
    payload = json.dumps(data, indent=2, sort_keys=True).encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, payload)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def read(skill_dir: Path) -> dict[str, Any] | None:
    """Return the full stats dict for ``skill_dir``, or ``None`` if absent."""
    sidecar = _sidecar(skill_dir)
    if not sidecar.exists():
        return None
    try:
        with _locked(sidecar) as fd:
            return _read_fd(fd)
    except OSError:
        return None


def record_view(skill_dir: Path) -> None:
    """Increment ``usage_count`` and refresh ``last_used_at``. Swallow errors."""
    sidecar = _sidecar(skill_dir)
    try:
        with _locked(sidecar) as fd:
            data = _read_fd(fd)
            data["usage_count"] = int(data.get("usage_count", 0)) + 1
            data["last_used_at"] = _now_iso()
            _write_fd(fd, data)
    except OSError:
        return


def record_outcome(
    skill_dir: Path, *, ok: bool, error_summary: str | None = None
) -> None:
    """Update success/error counters based on run outcome. Swallow errors."""
    sidecar = _sidecar(skill_dir)
    try:
        with _locked(sidecar) as fd:
            data = _read_fd(fd)
            if ok:
                data["success_count"] = int(data.get("success_count", 0)) + 1
            else:
                data["error_count"] = int(data.get("error_count", 0)) + 1
                data["last_error_at"] = _now_iso()
                if error_summary:
                    trimmed = error_summary.strip()[:_ERROR_SUMMARY_MAX_CHARS]
                    data["last_error_summary"] = trimmed or None
            _write_fd(fd, data)
    except OSError:
        return


def mark_optimized(skill_dir: Path) -> None:
    """Stamp ``last_optimized_at`` (used by the evolve approval flow)."""
    sidecar = _sidecar(skill_dir)
    try:
        with _locked(sidecar) as fd:
            data = _read_fd(fd)
            data["last_optimized_at"] = _now_iso()
            _write_fd(fd, data)
    except OSError:
        return
