"""Char-bounded memory: MEMORY.md (agent notes) + USER.md (user profile)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lite_horse.constants import (
    ENTRY_DELIMITER,
    MEMORY_CHAR_LIMIT,
    USER_PROFILE_CHAR_LIMIT,
    litehorse_home,
)

_INVISIBLE_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F]")
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s*:\s*you\s+are", re.I),
    re.compile(r"<\s*\|im_start\s*\|>", re.I),
]


class MemoryFull(Exception):  # noqa: N818
    """Raised when adding/replacing an entry would exceed the char limit."""

    def __init__(self, current: int, limit: int, attempted: int) -> None:
        super().__init__(
            f"Memory at {current}/{limit} chars. Adding this entry ({attempted} chars) "
            f"would exceed the limit."
        )
        self.current = current
        self.limit = limit
        self.attempted = attempted


class UnsafeMemoryContent(Exception):  # noqa: N818
    """Raised when an entry contains invisible Unicode or injection patterns."""


@dataclass
class MemoryStore:
    """One file = one store. Two instances: MEMORY.md and USER.md."""

    path: Path
    char_limit: int
    label: str  # "MEMORY" or "USER PROFILE"

    @classmethod
    def for_memory(cls) -> MemoryStore:
        p = litehorse_home() / "memories" / "MEMORY.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        return cls(path=p, char_limit=MEMORY_CHAR_LIMIT, label="MEMORY")

    @classmethod
    def for_user(cls) -> MemoryStore:
        p = litehorse_home() / "memories" / "USER.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
        return cls(path=p, char_limit=USER_PROFILE_CHAR_LIMIT, label="USER PROFILE")

    # ---------- read ----------
    def entries(self) -> list[str]:
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        return [e.strip() for e in raw.split(ENTRY_DELIMITER.strip()) if e.strip()]

    def total_chars(self) -> int:
        es = self.entries()
        return sum(len(e) for e in es) + max(0, len(es) - 1) * len(ENTRY_DELIMITER)

    # ---------- write ----------
    def add(self, content: str) -> None:
        self._validate(content)
        existing = self.entries()
        if content in existing:
            return  # duplicate guard
        new_total = self.total_chars() + len(content) + (len(ENTRY_DELIMITER) if existing else 0)
        if new_total > self.char_limit:
            raise MemoryFull(self.total_chars(), self.char_limit, len(content))
        existing.append(content)
        self._save(existing)

    def replace(self, old_substring: str, new_content: str) -> None:
        self._validate(new_content)
        entries = self.entries()
        matches = [i for i, e in enumerate(entries) if old_substring in e]
        if not matches:
            raise ValueError(f"no entry matches substring: {old_substring!r}")
        if len(matches) > 1:
            raise ValueError(
                f"substring {old_substring!r} matches {len(matches)} entries; "
                "be more specific"
            )
        entries[matches[0]] = new_content
        total = sum(len(e) for e in entries) + max(0, len(entries) - 1) * len(ENTRY_DELIMITER)
        if total > self.char_limit:
            raise MemoryFull(total, self.char_limit, len(new_content))
        self._save(entries)

    def remove(self, substring: str) -> None:
        entries = self.entries()
        matches = [i for i, e in enumerate(entries) if substring in e]
        if not matches:
            raise ValueError(f"no entry matches substring: {substring!r}")
        if len(matches) > 1:
            raise ValueError(
                f"substring {substring!r} matches {len(matches)} entries; be more specific"
            )
        entries.pop(matches[0])
        self._save(entries)

    # ---------- system prompt rendering (frozen snapshot at session start) ----------
    def render_block(self) -> str:
        entries = self.entries()
        if not entries:
            return ""
        chars = self.total_chars()
        pct = round(chars / self.char_limit * 100)
        body = ENTRY_DELIMITER.join(entries)
        rule = "═" * 46
        return (
            f"{rule}\n"
            f"{self.label} [{pct}% — {chars}/{self.char_limit} chars]\n"
            f"{rule}\n"
            f"{body}"
        )

    # ---------- internals ----------
    def _validate(self, content: str) -> None:
        if not content.strip():
            raise ValueError("empty memory entry")
        if _INVISIBLE_RE.search(content):
            raise UnsafeMemoryContent("memory entry contains invisible Unicode characters")
        for pat in _INJECTION_PATTERNS:
            if pat.search(content):
                raise UnsafeMemoryContent(
                    f"memory entry matches injection pattern: {pat.pattern}"
                )

    def _save(self, entries: list[str]) -> None:
        text = ENTRY_DELIMITER.join(entries)
        self.path.write_text(text + "\n" if text else "", encoding="utf-8")
