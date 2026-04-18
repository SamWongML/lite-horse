"""Skills capability + first-run sync of bundled skills into the state dir."""
from __future__ import annotations

import shutil
from pathlib import Path

from agents.sandbox.capabilities import LocalDirLazySkillSource, Skills
from agents.sandbox.entries import LocalDir

from hermes_lite.constants import hermeslite_home

_BUNDLED_DIR = Path(__file__).parent / "bundled"


def skills_root() -> Path:
    """Return the user's writable skills directory, ensuring it exists."""
    p = hermeslite_home() / "skills"
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


def make_skills_capability() -> Skills:
    """Build the SDK :class:`Skills` capability backed by ``~/.hermeslite/skills``."""
    return Skills(
        lazy_from=LocalDirLazySkillSource(source=LocalDir(src=skills_root())),
    )
