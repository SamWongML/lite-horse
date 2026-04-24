"""Persistent bottom toolbar shown beneath the prompt.

prompt_toolkit re-renders ``bottom_toolbar`` on each keystroke; we hand it a
closure that reads the live :class:`ReplState`. Format mirrors the spec in
``docs/plans/v0.3-cli-entrypoint.md``::

    <model>  session:<key[:8]>  ctx:<used>/<max> (pct%)  $<cost>  [<mode>]

Hidden during ``rich.live.Live`` streaming (a documented prompt_toolkit
limitation); the loop prints a one-line status above the Live block so the
user keeps the same context while output streams.

Pure-Python format helpers are kept import-light so toolbar tests don't pay
for prompt_toolkit. The ``html_renderer`` builds an ``HTML`` instance only
on demand.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def format_toolbar(
    *,
    model: str,
    session_key: str,
    total_tokens: int,
    ctx_max: int,
    cost_usd: float | None,
    permission_mode: str,
) -> str:
    """Plain-text toolbar string. Stable for tests + plain-mode fallback."""
    short = (session_key or "—")[:8]
    pct = (total_tokens / ctx_max * 100.0) if ctx_max > 0 else 0.0
    cost_str = "$—" if cost_usd is None else f"${cost_usd:.4f}"
    return (
        f"{model or '—'}  "
        f"session:{short}  "
        f"ctx:{total_tokens}/{ctx_max} ({pct:.1f}%)  "
        f"{cost_str}  "
        f"[{permission_mode}]"
    )


def build_bottom_toolbar(state: Any) -> Callable[[], Any]:
    """Return a callable that prompt_toolkit invokes per refresh.

    The callable returns prompt_toolkit ``HTML`` so the toolbar can be styled
    in terminals that support it.
    """
    from prompt_toolkit.formatted_text import HTML

    def _render() -> Any:
        text = format_toolbar(
            model=getattr(state, "model", "—"),
            session_key=getattr(state, "session_key", ""),
            total_tokens=int(getattr(state, "total_tokens", 0) or 0),
            ctx_max=int(getattr(state, "ctx_max", 0) or 0),
            cost_usd=getattr(state, "total_cost_usd", None),
            permission_mode=getattr(state, "permission_mode", "auto"),
        )
        # Escape angle brackets so HTML doesn't try to interpret ``<`` in
        # session keys / model names as tags.
        safe = text.replace("<", "&lt;").replace(">", "&gt;")
        return HTML(safe)

    return _render
