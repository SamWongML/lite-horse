"""Bundled-skill sync + skills-root path helper.

The SDK's ``Skills`` capability was explored in v0.1 but never wired into
``Agent(...)`` (the SDK has no ``capabilities`` kwarg on ``Agent.__init__``).
v0.2 Phase 14 replaces that dead path with an explicit ``skill_view`` tool.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from lite_horse.constants import litehorse_home

_BUNDLED_DIR = Path(__file__).parent.parent / "bundled" / "skills"


def skills_root() -> Path:
    """Return the user's writable skills directory, ensuring it exists."""
    p = litehorse_home() / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sync_bundled_skills() -> list[str]:
    """Copy each bundled skill into the user's skills dir if absent.

    Idempotent: existing skill directories are left untouched so the user (or
    the agent) can edit them without our copy clobbering their work.
    Returns the list of skill names that were freshly synced.
    """
    if not _BUNDLED_DIR.exists():
        return []
    dest_root = skills_root()
    synced: list[str] = []
    for src in _BUNDLED_DIR.iterdir():
        if not src.is_dir():
            continue
        dst = dest_root / src.name
        if dst.exists():
            continue
        shutil.copytree(src, dst)
        synced.append(src.name)
    return synced
