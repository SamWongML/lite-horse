"""Bounded picker utilities used by ``/resume``, ``/model`` and friends.

Two back-ends: prompt_toolkit ``radiolist_dialog`` (always available), and an
``fzf`` subprocess fallback used only when the terminal environment already
has ``fzf`` installed and the caller explicitly opts in. Heavy imports stay
inside function bodies so the module is safe to load on the ``--help``
fast-path.
"""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PickerItem:
    """One row in a picker.

    ``value`` is returned to the caller; ``label`` is what the user sees.
    """

    value: str
    label: str


def has_fzf() -> bool:
    return shutil.which("fzf") is not None


async def pick_one(
    title: str,
    items: Iterable[PickerItem],
    *,
    use_fzf: bool = False,
) -> str | None:
    """Present ``items`` and return the chosen ``value`` (or ``None`` on cancel).

    ``use_fzf=True`` and ``fzf`` on PATH → subprocess picker (synchronous but
    cheap); otherwise the prompt_toolkit radiolist dialog is used.
    """
    materialized = list(items)
    if not materialized:
        return None
    if use_fzf and has_fzf():
        return _pick_with_fzf(materialized)
    return await _pick_with_radiolist(title, materialized)


async def _pick_with_radiolist(title: str, items: list[PickerItem]) -> str | None:
    from prompt_toolkit.shortcuts import radiolist_dialog

    values: list[tuple[str, Any]] = [(it.value, it.label) for it in items]
    app = radiolist_dialog(title=title, values=values)
    result: str | None = await app.run_async()
    return result


def _pick_with_fzf(items: list[PickerItem]) -> str | None:
    lines = [f"{it.value}\t{it.label}" for it in items]
    try:
        completed = subprocess.run(
            ["fzf", "--with-nth", "2..", "--delimiter", "\t"],
            input="\n".join(lines),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    chosen = completed.stdout.strip().split("\t", 1)
    return chosen[0] if chosen and chosen[0] else None
