"""Variant generation + diversity pruning for the GEPA loop.

Given a baseline ``SKILL.md`` and a generator callable, produce K
mutated variants per generation. The generator is a free-form callable
``(baseline, hint) -> str`` so the caller can plug in an OpenAI
``chat.completions`` call, an Anthropic ``messages.create``, or a
fixture for tests.

Diversity pruning runs after generation: two variants whose cosine
similarity is ≥ :data:`GEPA_DIVERSITY_COSINE` are collapsed to one
(the first), so the population doesn't waste cost scoring near
duplicates.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from lite_horse.constants import GEPA_DEFAULT_POPULATION_SIZE, GEPA_DIVERSITY_COSINE

VariantGenerator = Callable[[str, str], str]
"""``(baseline, mutation_hint) -> variant_skill_md``."""

Embedder = Callable[[str], list[float]]


@dataclass(frozen=True)
class Variant:
    """One SKILL.md candidate inside a GEPA generation."""

    skill_md: str
    mutation: str  # human-readable label of the mutation that produced it


_DEFAULT_MUTATION_HINTS: tuple[str, ...] = (
    "rephrase the Procedure section in clearer imperative voice",
    "add 1-3 concrete Pitfalls examples drawn from the description",
    "reorder steps so the verification step happens before any write",
    "tighten the description so every sentence carries a load-bearing fact",
    "add a 'When to use' block at the top if missing",
    "merge any duplicated guidance and remove dead links",
    "add an explicit failure-recovery branch to the Procedure",
    "split the longest paragraph into two shorter ones for readability",
)


def generate_population(
    baseline: str,
    *,
    generator: VariantGenerator,
    size: int = GEPA_DEFAULT_POPULATION_SIZE,
    mutation_hints: tuple[str, ...] = _DEFAULT_MUTATION_HINTS,
) -> list[Variant]:
    """Produce ``size`` mutated variants of ``baseline``.

    The ``mutation_hints`` cycle ensures the K calls explore distinct
    directions even when the generator is greedy. The baseline itself
    is included as an "identity" variant so a no-op evolution can still
    win the generation if every mutation regresses.
    """
    variants: list[Variant] = [Variant(skill_md=baseline, mutation="baseline")]
    for i in range(size - 1):
        hint = mutation_hints[i % len(mutation_hints)]
        try:
            body = generator(baseline, hint)
        except Exception:
            continue
        if body and body != baseline:
            variants.append(Variant(skill_md=body, mutation=hint))
    return variants


def diversity_prune(
    variants: list[Variant],
    *,
    embedder: Embedder,
    threshold: float = GEPA_DIVERSITY_COSINE,
) -> list[Variant]:
    """Drop near-duplicates by greedy cosine pairwise comparison.

    Keeps the first occurrence of each cluster. The baseline (if
    present at index 0) is always retained so the loop has a known
    floor to beat.
    """
    if len(variants) <= 1:
        return list(variants)
    embeddings: list[list[float]] = []
    for v in variants:
        try:
            embeddings.append(list(embedder(v.skill_md)))
        except Exception:
            embeddings.append([])
    kept: list[Variant] = []
    kept_embeds: list[list[float]] = []
    for v, e in zip(variants, embeddings, strict=False):
        is_dup = False
        if e:
            for ke in kept_embeds:
                if not ke:
                    continue
                if _cosine(e, ke) >= threshold:
                    is_dup = True
                    break
        if not is_dup:
            kept.append(v)
            kept_embeds.append(e)
    return kept


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
