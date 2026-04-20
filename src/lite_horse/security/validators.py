"""Shared validators for any untrusted string that may land in the system prompt.

Two sinks currently rely on these patterns: ``MemoryStore`` entries and
agent-managed ``SKILL.md`` content. Both feed ``instructions.py`` on the next
turn, so an injection payload here becomes an injection payload there.
"""
from __future__ import annotations

import re

_INVISIBLE_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F]")
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s*:\s*you\s+are", re.I),
    re.compile(r"<\s*\|im_start\s*\|>", re.I),
)


class UnsafeContent(Exception):  # noqa: N818
    """Raised when a string contains invisible Unicode or a known injection pattern."""


def check_untrusted(content: str) -> None:
    """Raise ``UnsafeContent`` if ``content`` is unsafe to embed in a system prompt."""
    if _INVISIBLE_RE.search(content):
        raise UnsafeContent("content contains invisible Unicode characters")
    for pat in _INJECTION_PATTERNS:
        if pat.search(content):
            raise UnsafeContent(f"content matches injection pattern: {pat.pattern}")
