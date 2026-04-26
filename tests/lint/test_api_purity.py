"""`import lite_horse.api` MUST NOT pull in `web` / `scheduler` / `worker`.

The v0.3 in-process surface (`lite_horse.api`) is consumed by the v0.2
webapp directly — it must stay free of the FastAPI / async-engine /
scheduler stacks introduced in v0.4. A regression here means the webapp
now boots a database engine on import.
"""
from __future__ import annotations

import subprocess
import sys


def test_import_lite_horse_api_does_not_load_web_layers() -> None:
    # Run in a fresh interpreter: pytest itself may have imported web during
    # earlier tests, which would mask a real leak.
    code = (
        "import sys, json\n"
        "import lite_horse.api  # noqa: F401\n"
        "leaked = sorted(\n"
        "    m for m in sys.modules\n"
        "    if any(\n"
        "        m == p or m.startswith(p + '.')\n"
        "        for p in ('lite_horse.web', 'lite_horse.scheduler', 'lite_horse.worker')\n"
        "    )\n"
        ")\n"
        "print(json.dumps(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    leaked = result.stdout.strip().splitlines()[-1]
    assert leaked == "[]", (
        f"`import lite_horse.api` leaked v0.4 modules: {leaked}"
    )
