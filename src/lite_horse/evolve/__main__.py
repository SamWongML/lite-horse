"""Entry point for ``python -m lite_horse.evolve``."""
from __future__ import annotations

import sys

from lite_horse.evolve.cli import main

if __name__ == "__main__":
    sys.exit(main())
