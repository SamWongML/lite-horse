"""Cloud SDK imports are confined to `src/lite_horse/storage/`.

Per the v0.4 Hard Contract: only the storage layer is allowed to import
`boto3` / `aiobotocore` / `aioboto3` / `redis` / `aws_encryption_sdk` /
`aws_secretsmanager_caching` / `botocore`. Everything else talks to those
clouds via the storage Protocols.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "lite_horse"
ALLOWED_ROOT = SRC / "storage"

FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(boto3|botocore|aiobotocore|aioboto3|redis|"
    r"aws_encryption_sdk|aws_secretsmanager_caching)\b"
)


def _python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if p.is_file()]


def test_no_cloud_imports_outside_storage() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if str(path).startswith(str(ALLOWED_ROOT)):
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if FORBIDDEN.search(line):
                rel = path.relative_to(REPO)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "cloud SDK imports must live in src/lite_horse/storage/ only:\n  "
        + "\n  ".join(offenders)
    )
