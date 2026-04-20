"""Shared slug regex for skill names — imported by manage_tool and view_tool."""
from __future__ import annotations

import re

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
