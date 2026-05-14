"""Tool / hook bodies must route durable state through backends.

Asserts that no module under ``agent/`` (with the explicit
``agent/backends/`` exception) and no module among the named tool files
imports ``litehorse_home``, ``MemoryStore``, ``skills_root``, or any
``*Repo`` class directly. The only allowed seam is the backend Protocol
from :mod:`lite_horse.agent.backends`.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "lite_horse"

# Files / dirs subject to the rule.
SCOPE_FILES: tuple[Path, ...] = (
    SRC / "memory" / "tool.py",
    SRC / "skills" / "manage_tool.py",
    SRC / "skills" / "view_tool.py",
    SRC / "cron" / "manage_tool.py",
    SRC / "skills" / "stats.py",
)
SCOPE_DIRS: tuple[Path, ...] = (SRC / "agent",)

# Wrappers — the explicit allowlist. These ARE the seam.
ALLOWED_PREFIXES: tuple[Path, ...] = (
    SRC / "agent" / "backends",
)

# Direct names whose import is banned in scope.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "litehorse_home",
        "MemoryStore",
        "skills_root",
        # Repo classes — exhaustive list of *Repo classes in
        # src/lite_horse/repositories/. ``BaseRepo`` is the abstract base
        # and isn't on the list because it carries no tenant data; the
        # concrete subclasses do.
        "MemoryRepo",
        "SkillRepo",
        "SkillProposalRepo",
        "CronRepo",
        "ByoKeyStore",
        "AuditRepo",
        "CommandRepo",
        "InstructionRepo",
        "McpRepo",
        "MessageRepo",
        "OptOutRepo",
        "SessionRepo",
        "UsageRepo",
        "UserRepo",
        "UserSettingsRepo",
    }
)

# Forbid `import lite_horse.constants` followed by a `litehorse_home`
# reference: not enforced — direct-name import is the contract. Modules
# can still depend on constants for non-litehorse_home symbols.


def _python_files() -> list[Path]:
    files: list[Path] = []
    for path in SCOPE_FILES:
        if path.is_file():
            files.append(path)
    for d in SCOPE_DIRS:
        for p in d.rglob("*.py"):
            if any(
                str(p) == str(prefix) or str(p).startswith(str(prefix) + "/")
                for prefix in ALLOWED_PREFIXES
            ):
                continue
            files.append(p)
    return files


def _imported_names(tree: ast.AST) -> list[str]:
    """Yield every name introduced by an ``import`` / ``from`` statement."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name.rsplit(".", 1)[-1])
    return names


def test_no_forbidden_imports_in_scope() -> None:
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported_names(tree):
            if name in FORBIDDEN_NAMES:
                offenders.append(
                    f"{path.relative_to(REPO)} imports forbidden name {name!r}"
                )
    assert offenders == [], (
        "Tool/hook bodies must route through "
        "agent/backends/, not import durable-state primitives directly. "
        "Offenders:\n  " + "\n  ".join(offenders)
    )
