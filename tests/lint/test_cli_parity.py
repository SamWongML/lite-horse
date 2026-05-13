"""Phase 40 + 46 hard gate: every backend Protocol ships local + cloud impls.

For every Protocol under ``src/lite_horse/agent/backends/<name>.py`` there
must be exactly one ``<name>_local.py`` and one ``<name>_cloud.py`` next
to it, both implementing the full Protocol method set with matching
signatures. The CLI parity invariant from the v0.5 hard contract stays
enforced as new backends land — Phase 46 makes the discovery automatic so
adding a new Protocol can't slip past the gate.

The signature check compares positional + keyword arg names and the
``async def`` / ``def`` form. Return-type drift is not flagged here
because mypy already enforces the Protocol contract at type-check time.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKENDS_DIR = REPO / "src" / "lite_horse" / "agent" / "backends"

# Class-name overrides for Protocols that diverge from the
# ``<stem>.title() + "Backend"`` convention.
PROTOCOL_CLASS_OVERRIDES: dict[str, str] = {
    "feedback": "FeedbackSink",
}
IMPL_CLASS_OVERRIDES: dict[str, tuple[str, str]] = {
    # stem -> (local class, cloud class)
    "feedback": ("FeedbackLocalBackend", "FeedbackCloudBackend"),
}


def _discover_protocols() -> list[str]:
    """Return the sorted list of backend stems present in ``backends/``.

    A stem ``X`` qualifies as a Protocol if ``X.py`` exists alongside
    ``X_local.py`` and ``X_cloud.py``. Files like ``__init__.py`` and
    the helper modules without paired impls are skipped.
    """
    stems: set[str] = set()
    for path in BACKENDS_DIR.glob("*.py"):
        if path.name.startswith("_"):
            continue
        stem = path.stem
        if stem.endswith("_local") or stem.endswith("_cloud"):
            continue
        if (
            (BACKENDS_DIR / f"{stem}_local.py").is_file()
            and (BACKENDS_DIR / f"{stem}_cloud.py").is_file()
        ):
            stems.add(stem)
    return sorted(stems)


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


def _read_class_methods(
    path: Path, class_name: str
) -> dict[str, tuple[bool, tuple[str, ...]]]:
    """Return ``{method_name: (is_async, (arg_names, ...))}``.

    Skips dunders and underscore-prefixed helpers. ``self`` is dropped
    from the arg tuple so static/classmethods compare cleanly.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        out: dict[str, tuple[bool, tuple[str, ...]]] = {}
        for child in node.body:
            if not isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)):
                continue
            if child.name.startswith("_"):
                continue
            args = child.args
            names: list[str] = [
                a.arg for a in args.posonlyargs + args.args + args.kwonlyargs
                if a.arg != "self"
            ]
            out[child.name] = (
                isinstance(child, ast.AsyncFunctionDef),
                tuple(names),
            )
        return out
    return {}


# At import time, fail loudly if discovery returns nothing — that would
# mean the backends dir moved and silently turned the gate into a no-op.
DISCOVERED_PROTOCOLS: tuple[str, ...] = tuple(_discover_protocols())
assert DISCOVERED_PROTOCOLS, (
    f"CLI parity discovery found no backend Protocols under {BACKENDS_DIR}; "
    "the lint gate would silently pass — refusing to run."
)


def test_backends_have_local_and_cloud_impls() -> None:
    missing: list[str] = []
    for name in DISCOVERED_PROTOCOLS:
        proto = BACKENDS_DIR / f"{name}.py"
        local = BACKENDS_DIR / f"{name}_local.py"
        cloud = BACKENDS_DIR / f"{name}_cloud.py"
        for needed in (proto, local, cloud):
            if not needed.is_file():
                missing.append(str(needed.relative_to(REPO)))
    assert not missing, (
        "CLI parity (Phase 40/46 hard gate): missing backend impls:\n  "
        + "\n  ".join(missing)
    )


def test_local_and_cloud_implement_full_protocol_method_set() -> None:
    drift: list[str] = []
    for name in DISCOVERED_PROTOCOLS:
        proto_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}.py", _expected_class_name(name)
        )
        local_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}_local.py", _local_class_name(name)
        )
        cloud_methods = _read_class_methods(
            BACKENDS_DIR / f"{name}_cloud.py", _cloud_class_name(name)
        )
        for impl_label, impl in (
            ("local", local_methods),
            ("cloud", cloud_methods),
        ):
            missing = set(proto_methods) - set(impl)
            if missing:
                drift.append(
                    f"{name}_{impl_label} missing methods: {sorted(missing)}"
                )
    assert not drift, (
        "CLI parity (Phase 40/46 hard gate): backend method-set drift "
        "between Protocol and impls:\n  " + "\n  ".join(drift)
    )


def test_local_and_cloud_have_matching_signatures() -> None:
    """Hard-gate Phase 46: arg names + ``async`` form must match Protocol.

    A new arg added to one impl but not the other is a CLI/cloud parity
    break — the Protocol is the contract, both impls must mirror it.
    """
    drift: list[str] = []
    for name in DISCOVERED_PROTOCOLS:
        proto = _read_class_methods(
            BACKENDS_DIR / f"{name}.py", _expected_class_name(name)
        )
        local = _read_class_methods(
            BACKENDS_DIR / f"{name}_local.py", _local_class_name(name)
        )
        cloud = _read_class_methods(
            BACKENDS_DIR / f"{name}_cloud.py", _cloud_class_name(name)
        )
        for method, proto_sig in proto.items():
            for impl_label, impl in (("local", local), ("cloud", cloud)):
                impl_sig = impl.get(method)
                if impl_sig is None:
                    continue  # handled by the method-set test above.
                if impl_sig != proto_sig:
                    drift.append(
                        f"{name}_{impl_label}.{method}: signature "
                        f"{impl_sig} != Protocol {proto_sig}"
                    )
    assert not drift, (
        "CLI parity (Phase 46 hard gate): backend signature drift "
        "between Protocol and impls:\n  " + "\n  ".join(drift)
    )
