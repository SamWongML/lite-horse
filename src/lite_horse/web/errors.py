"""Domain `ErrorKind` enum mapped to `HTTPException` status codes.

Each route surfaces failures by raising `http_error(ErrorKind.X, msg)`;
the resulting JSON body is `{"kind": "X", "message": "..."}`. The webapp
client switches on the stable `kind` string, never on HTTP status alone.
"""
from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException


class ErrorKind(StrEnum):
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    RATE_LIMIT = "RATE_LIMIT"
    TOOL_DENIED = "TOOL_DENIED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL = "INTERNAL"


_KIND_TO_STATUS: dict[ErrorKind, int] = {
    ErrorKind.UNAUTHORIZED: 401,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.RATE_LIMIT: 429,
    ErrorKind.TOOL_DENIED: 403,
    ErrorKind.UNAVAILABLE: 503,
    ErrorKind.INTERNAL: 500,
}


def http_error(kind: ErrorKind, detail: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=_KIND_TO_STATUS[kind],
        detail={"kind": kind.value, "message": detail or kind.value},
    )
