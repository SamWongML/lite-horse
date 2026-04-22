"""Hard gates that a candidate SKILL.md must clear before we write a proposal.

Each gate returns a ``(passed, reason)`` pair. The runner aggregates them into
a structured result so the admin UI can show which gate rejected a candidate.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import yaml

from lite_horse.constants import SKILL_MAX_BYTES
from lite_horse.security.validators import UnsafeContent, check_untrusted

COSINE_MIN = 0.75

PytestRunner = Callable[[], bool]


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    reason: str = ""


def check_size(candidate: str) -> GateResult:
    size = len(candidate.encode("utf-8"))
    if size > SKILL_MAX_BYTES:
        return GateResult("size", False, f"{size} > {SKILL_MAX_BYTES} bytes")
    return GateResult("size", True)


def check_injection(candidate: str) -> GateResult:
    try:
        check_untrusted(candidate)
    except UnsafeContent as e:
        return GateResult("injection", False, str(e))
    return GateResult("injection", True)


def check_frontmatter(  # noqa: PLR0911 — branch-per-failure keeps the reasons inline
    candidate: str,
    *,
    baseline_name: str,
    baseline_version: int | None,
) -> GateResult:
    """Validate YAML frontmatter: preserves ``name:``, bumps ``version:``."""
    if not candidate.startswith("---"):
        return GateResult("frontmatter", False, "missing opening '---'")
    end = candidate.find("\n---", 3)
    if end == -1:
        return GateResult("frontmatter", False, "missing closing '---'")
    try:
        fm = yaml.safe_load(candidate[3:end])
    except yaml.YAMLError as e:
        return GateResult("frontmatter", False, f"invalid YAML: {e}")
    if not isinstance(fm, dict):
        return GateResult("frontmatter", False, "frontmatter is not a mapping")
    if fm.get("name") != baseline_name:
        return GateResult(
            "frontmatter",
            False,
            f"name changed: {fm.get('name')!r} != {baseline_name!r}",
        )
    new_version = fm.get("version")
    if not isinstance(new_version, int):
        return GateResult("frontmatter", False, "version: must be an integer")
    expected_min = (baseline_version or 0) + 1
    if new_version < expected_min:
        return GateResult(
            "frontmatter",
            False,
            f"version {new_version} must be >= {expected_min}",
        )
    return GateResult("frontmatter", True)


def check_cosine(cosine: float) -> GateResult:
    if cosine < COSINE_MIN:
        return GateResult("cosine", False, f"{cosine:.3f} < {COSINE_MIN}")
    return GateResult("cosine", True)


def check_pytest(runner: PytestRunner | None) -> GateResult:
    """Run the project's test suite with the candidate staged on disk.

    The runner is injectable so unit tests don't spawn pytest recursively.
    """
    if runner is None:
        runner = _default_pytest_runner
    try:
        ok = runner()
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        return GateResult("pytest", False, f"runner crashed: {e}")
    return GateResult("pytest", ok, "" if ok else "test suite failed")


def _default_pytest_runner() -> bool:
    proc = subprocess.run(
        ["pytest", "-q", "-x", "--no-header"],
        capture_output=True,
        timeout=600,
        check=False,
    )
    return proc.returncode == 0
