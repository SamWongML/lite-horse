"""`@arun` decorator — adapts `async def` command bodies to sync Click/Typer.

Every async CLI command body is wrapped so `asyncio.run` is called exactly
once per invocation. The REPL loop has its own `asyncio.run` in
`cli.repl.loop.main_loop` and does not use this decorator.
"""
from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def arun(fn: Callable[P, Awaitable[R]]) -> Callable[P, R]:
    """Wrap an async function so it runs to completion under `asyncio.run`."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        coro: Any = fn(*args, **kwargs)
        return asyncio.run(coro)  # type: ignore[no-any-return]

    return wrapper
