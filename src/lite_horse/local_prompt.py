"""CLI-only prompt extras: SOUL persona + project AGENTS.md.

Lives at the package root so the agent layer (which is bound by the
"no ``litehorse_home`` import" rule) can stay clean. The CLI factory passes
:func:`load_local_prompt_extras` into ``make_instructions(extras_loader=...)``;
cloud mode skips this module entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from lite_horse.constants import litehorse_home


@dataclass(frozen=True)
class LocalPromptExtras:
    """CLI-only extras: SOUL persona + project AGENTS.md.

    Cloud-mode prompts skip these (the equivalents are layered into
    ``EffectiveConfig.instructions``).
    """

    soul: str = ""
    agents_md: str = ""

    @classmethod
    def empty(cls) -> LocalPromptExtras:
        return cls()


def _read_optional(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars].strip()
    except (FileNotFoundError, OSError):
        return ""


def load_local_prompt_extras() -> LocalPromptExtras:
    """Read SOUL + AGENTS.md fresh on every call. Empty strings on missing files."""
    home = litehorse_home()
    return LocalPromptExtras(
        soul=_read_optional(home / "soul.md", 8_000),
        agents_md=_read_optional(home / "AGENTS.md", 4_000),
    )
