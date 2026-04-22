"""Conditional skill activation (Phase 21).

Filters the on-disk skill library down to a small top-K list per turn so the
prompt only lists skills that are plausibly relevant to the current user
message. Skills declare their triggers via optional ``activate_when:`` and
``category:`` frontmatter; skills without ``activate_when`` are "always-on"
and score a small default so they still surface when there aren't enough
stronger signals.

Heuristic (no embeddings — per-turn latency matters):

- keyword in user text .......... +2
- keyword present in USER.md .... +1
- file glob matches a user token  +2
- always-on (no activate_when) ..  0.5 baseline

Top-K by score; ties broken by name. Zero-score, non-always-on skills drop
out. Callers pass ``user_text=None`` to bypass filtering (fallback path).
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lite_horse.constants import ACTIVATION_TOP_K, litehorse_home

_ALWAYS_ON_SCORE = 0.5
_KEYWORD_IN_TEXT_SCORE = 2.0
_KEYWORD_IN_PROFILE_SCORE = 1.0
_GLOB_MATCH_SCORE = 2.0

_TOKEN_SPLIT = re.compile(r"[\s,;:!?\"'()\[\]{}<>]+")


@dataclass
class SkillEntry:
    """Parsed view of one SKILL.md head — enough to score and render it."""

    name: str
    description: str
    category: str | None = None
    activate_when: list[dict[str, list[str]]] = field(default_factory=list)

    @property
    def always_on(self) -> bool:
        return not self.activate_when


def filter_for_turn(
    *,
    skills_dir: Path,
    user_text: str | None,
    top_k: int = ACTIVATION_TOP_K,
) -> list[SkillEntry]:
    """Return the skills that should render in this turn's prompt index.

    ``user_text=None`` means "no activation signal available" — we fall back
    to returning every skill alphabetically, uncapped, so the agent still
    sees the full index when extraction fails.
    """
    entries = _load_all(skills_dir)
    if not entries:
        return []
    if user_text is None:
        return sorted(entries, key=lambda e: e.name)

    text_lower = user_text.lower()
    profile_lower = _read_user_profile_text().lower()

    scored: list[tuple[float, SkillEntry]] = []
    for entry in entries:
        score = _score_entry(entry, user_text, text_lower, profile_lower)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda t: (-t[0], t[1].name))
    return [entry for _, entry in scored[:top_k]]


def _load_all(skills_dir: Path) -> list[SkillEntry]:
    if not skills_dir.is_dir():
        return []
    out: list[SkillEntry] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        out.append(_parse_skill(child.name, skill_md))
    return out


def _parse_skill(name: str, skill_md: Path) -> SkillEntry:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return SkillEntry(name=name, description="")
    fm = _parse_frontmatter(text)
    description = str(fm.get("description") or "").strip()
    category_raw = fm.get("category")
    category = str(category_raw).strip() if isinstance(category_raw, str) else None
    activate_when = _coerce_rules(fm.get("activate_when"))
    return SkillEntry(
        name=name,
        description=description,
        category=category,
        activate_when=activate_when,
    )


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].lstrip("\n")
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_rules(raw: Any) -> list[dict[str, list[str]]]:
    if not isinstance(raw, list):
        return []
    rules: list[dict[str, list[str]]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule: dict[str, list[str]] = {}
        kws = item.get("keywords")
        if isinstance(kws, list):
            rule["keywords"] = [str(k) for k in kws if isinstance(k, str) and k.strip()]
        globs = item.get("file_globs")
        if isinstance(globs, list):
            rule["file_globs"] = [str(g) for g in globs if isinstance(g, str) and g.strip()]
        if rule:
            rules.append(rule)
    return rules


def _score_entry(
    entry: SkillEntry,
    user_text_raw: str,
    user_text_lower: str,
    user_profile_lower: str,
) -> float:
    if entry.always_on:
        return _ALWAYS_ON_SCORE
    score = 0.0
    for rule in entry.activate_when:
        for kw in rule.get("keywords") or []:
            kw_lower = kw.lower()
            if kw_lower and kw_lower in user_text_lower:
                score += _KEYWORD_IN_TEXT_SCORE
            elif kw_lower and user_profile_lower and kw_lower in user_profile_lower:
                score += _KEYWORD_IN_PROFILE_SCORE
        for glob in rule.get("file_globs") or []:
            if _glob_matches_any_token(glob, user_text_raw):
                score += _GLOB_MATCH_SCORE
    return score


def _glob_matches_any_token(glob: str, user_text: str) -> bool:
    for raw_tok in _TOKEN_SPLIT.split(user_text):
        tok = raw_tok.strip(".,")
        if tok and fnmatch.fnmatch(tok, glob):
            return True
    return False


def _read_user_profile_text() -> str:
    path = litehorse_home() / "memories" / "USER.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
