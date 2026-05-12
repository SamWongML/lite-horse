"""Phase 40: every backend Protocol ships both a local and a cloud impl.

For every Protocol under ``src/lite_horse/agent/backends/<name>.py`` there
must be exactly one ``<name>_local.py`` and one ``<name>_cloud.py`` next
to it, both exposing the full Protocol method set. Drift breaks CI so the
CLI parity invariant from the v0.5 hard contract stays enforced as new
backends land in later phases.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKENDS_DIR = REPO / "src" / "lite_horse" / "agent" / "backends"

# Phase 40 ships these three Protocols. Phase 42 adds ``recall``;
# Phase 44 adds ``feedback`` (its Protocol is named ``FeedbackSink`` rather
# than ``FeedbackBackend`` — semantically a write sink + projection — so
# the parity check looks up the explicit class name from this map first).
EXPECTED_PROTOCOLS: tuple[str, ...] = (
    "memory", "skill", "cron", "recall", "feedback",
)
PROTOCOL_CLASS_OVERRIDES: dict[str, str] = {
    "feedback": "FeedbackSink",
}
IMPL_CLASS_OVERRIDES: dict[str, tuple[str, str]] = {
    # stem -> (local class, cloud class)
    "feedback": ("FeedbackLocalBackend", "FeedbackCloudBackend"),
}


def _read_class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef))
                and not child.name.startswith("_")
            }
    return set()


def _expected_class_name(stem: str) -> str:
    if stem in PROTOCOL_CLASS_OVERRIDES:
        return PROTOCOL_CLASS_OVERRIDES[stem]
    return stem.title().replace("_", "") + "Backend"


def _local_class_name(stem: str) -> str:
    if stem in IMPL_CLASS_OVERRIDES:
        return IMPL_CLASS_OVERRIDES[stem][0]
    return f"{stem.title()}LocalBackend"


def _cloud_class_name(stem: str) -> str:
    if stem in IMPL_CLASS_OVERRIDES:
        return IMPL_CLASS_OVERRIDES[stem][1]
    return f"{stem.title()}CloudBackend"


def test_backends_have_local_and_cloud_impls() -> None:
    missing: list[str] = []
    for name in EXPECTED_PROTOCOLS:
        proto = BACKENDS_DIR / f"{name}.py"
        local = BACKENDS_DIR / f"{name}_local.py"
        cloud = BACKENDS_DIR / f"{name}_cloud.py"
        for needed in (proto, local, cloud):
            if not needed.is_file():
                missing.append(str(needed.relative_to(REPO)))
    assert not missing, (
        "Phase 40 CLI parity: missing backend impls:\n  "
        + "\n  ".join(missing)
    )


def test_local_and_cloud_implement_full_protocol_method_set() -> None:
    drift: list[str] = []
    for name in EXPECTED_PROTOCOLS:
        proto_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}.py", _expected_class_name(name)
        )
        local_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}_local.py", _local_class_name(name)
        )
        cloud_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}_cloud.py", _cloud_class_name(name)
        )
        for impl_name, impl_methods in (
            ("local", local_methods),
            ("cloud", cloud_methods),
        ):
            missing = proto_methods - impl_methods
            if missing:
                drift.append(
                    f"{name}_{impl_name} missing: {sorted(missing)}"
                )
    assert not drift, (
        "Phase 40 CLI parity: backend method-set drift between Protocol "
        "and impls:\n  " + "\n  ".join(drift)
    )
