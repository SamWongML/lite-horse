"""``python -m lite_horse.evolve <skill>`` admin shim.

Intentionally a separate module: the webapp runtime should never import this
path. Invoked manually by operators or from an admin-panel subprocess.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from lite_horse.evolve.runner import evolve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m lite_horse.evolve")
    parser.add_argument("skill", help="Skill slug (directory name under skills/).")
    parser.add_argument("--days", type=int, default=14, help="Trace-mining window.")
    args = parser.parse_args(argv)

    result = evolve(args.skill, days=args.days)
    print(json.dumps(asdict(result), indent=2))
    return 0 if result.approved else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
