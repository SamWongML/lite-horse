"""Score a candidate SKILL.md against its baseline.

Fitness has three components:

1. *Judge score* — LLM-as-judge rubric in ``[0, 1]``. Specificity, error
   coverage, no drift.
2. *Length penalty* — linear 0 → 0.2 as size approaches ``SKILL_MAX_BYTES``.
3. *Semantic cosine* — ``text-embedding-3-small`` similarity against the
   baseline, to detect purpose drift.

The final score is ``max(0, judge - length_penalty) * 0.7 + cosine * 0.3``. All
three model calls are injectable so tests stay hermetic.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass

import openai

from lite_horse.constants import SKILL_MAX_BYTES
from lite_horse.evolve.trace_miner import Trajectory

Judge = Callable[[str, str, list[Trajectory]], float]
Embedder = Callable[[str], list[float]]

_JUDGE_MODEL = "gpt-4o-mini"
_EMBED_MODEL = "text-embedding-3-small"


@dataclass(frozen=True)
class FitnessScore:
    judge: float
    length_penalty: float
    cosine: float
    total: float


def score(
    *,
    candidate: str,
    baseline: str,
    trajectories: list[Trajectory],
    judge: Judge,
    embedder: Embedder,
) -> FitnessScore:
    j = max(0.0, min(1.0, float(judge(candidate, baseline, trajectories))))
    penalty = _length_penalty(candidate)
    cos = _cosine(embedder(candidate), embedder(baseline))
    effective = max(0.0, j - penalty)
    total = effective * 0.7 + cos * 0.3
    return FitnessScore(judge=j, length_penalty=penalty, cosine=cos, total=total)


def _length_penalty(text: str) -> float:
    size = len(text.encode("utf-8"))
    ratio = size / SKILL_MAX_BYTES
    return min(0.2, max(0.0, ratio - 0.5) * 0.4)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------- default OpenAI-backed implementations ----------

def default_judge(candidate: str, baseline: str, trajectories: list[Trajectory]) -> float:
    """Real LLM judge. Not used in tests — pass an injected judge instead."""
    client = openai.OpenAI()
    rubric = (
        "You are scoring a revised SKILL.md against its baseline. Output JSON: "
        '{"score": <float in [0,1]>}. Criteria: does the revision address the '
        "failure trajectories? Is it more specific without drifting in purpose? "
        "Is it concise?"
    )
    trace_block = "\n\n".join(
        f"TASK: {t.task}\nRESPONSE: {t.response}\nOUTCOME: {t.outcome}"
        for t in trajectories
    ) or "(no trajectories provided)"
    user = (
        f"BASELINE:\n{baseline}\n\nCANDIDATE:\n{candidate}\n\n"
        f"FAILURE TRAJECTORIES:\n{trace_block}"
    )
    resp = client.chat.completions.create(
        model=_JUDGE_MODEL,
        messages=[{"role": "system", "content": rubric}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    payload = json.loads(resp.choices[0].message.content or "{}")
    return float(payload.get("score", 0.0))


def default_embedder(text: str) -> list[float]:
    """Real embedder backed by ``text-embedding-3-small``."""
    client = openai.OpenAI()
    resp = client.embeddings.create(model=_EMBED_MODEL, input=text)
    return list(resp.data[0].embedding)
