"""Deterministic local stubs for the GEPA loop — CLI parity backend.

The cloud GEPA path plugs an OpenAI/Anthropic generator, an embedding
model, an :class:`Agent` replay, and the Phase 44 classifier into
:func:`lite_horse.evolve.gepa.runner.run_gepa`. The CLI parity gate
needs the same loop to *run* offline without booting any of those, so
this module supplies model-free stand-ins:

* :func:`local_generator` appends a mutation hint as an HTML comment —
  keeps the body deterministic, never throws.
* :func:`local_embedder` returns a 27-bin character-frequency vector
  (a-z + other), enough for cosine pruning to recognise near-duplicates
  without an embedding model.
* :func:`local_run_case` replays a case by quoting the user request
  back; the variant body is reflected through a short prefix so the
  classifier can differentiate variants.
* :func:`local_classifier` reads the eval case's recorded rating out of
  the agent text echo and clamps to ``{-1, 0, 1}``.

These are deliberately dumb — the CLI gate is "did the loop run end to
end and emit a proposal," not "did evolution improve the skill."
"""
from __future__ import annotations

from collections.abc import Callable

from lite_horse.evolve.gepa.eval_set import EvalCase

_RATING_MARK = "⌖rating="


def local_generator() -> Callable[[str, str], str]:
    """Append the mutation hint as a trailing HTML comment."""

    def _gen(baseline: str, hint: str) -> str:
        return baseline.rstrip() + f"\n\n<!-- gepa mutation: {hint} -->\n"

    return _gen


def local_embedder() -> Callable[[str], list[float]]:
    """27-bin character-frequency vector (a-z plus "other")."""

    def _emb(text: str) -> list[float]:
        vec = [0.0] * 27
        for ch in text.lower():
            o = ord(ch) - ord("a")
            if 0 <= o < 26:
                vec[o] += 1.0
            else:
                vec[26] += 1.0
        total = sum(vec) or 1.0
        return [v / total for v in vec]

    return _emb


def local_run_case() -> Callable[[str, EvalCase], str]:
    """Echo the user request, embedding the case rating for the classifier.

    The classifier signature only sees ``(user_request, agent_text,
    reason)`` — not the eval case directly — so we smuggle the rating
    through the synthetic agent text via :data:`_RATING_MARK`.
    """

    def _run(variant_skill_md: str, case: EvalCase) -> str:
        del variant_skill_md  # unused: local replay is variant-agnostic
        return f"local-replay {_RATING_MARK}{case.rating} :: {case.user_request[:120]}"

    return _run


def local_classifier() -> Callable[[str, str, str], int]:
    """Recover the rating that :func:`local_run_case` embedded."""

    def _cls(user_request: str, agent_text: str, reason: str) -> int:
        del user_request, reason  # unused in the local heuristic
        idx = agent_text.find(_RATING_MARK)
        if idx < 0:
            return 0
        tail = agent_text[idx + len(_RATING_MARK):]
        sign = 1
        if tail.startswith("-"):
            sign = -1
            tail = tail[1:]
        digits = ""
        for ch in tail:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return 0
        return max(-1, min(1, sign * int(digits)))

    return _cls
