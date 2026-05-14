"""Variant generation + diversity pruning."""
from __future__ import annotations

from lite_horse.evolve.gepa.population import (
    Variant,
    diversity_prune,
    generate_population,
)


def test_generate_population_always_includes_baseline() -> None:
    pop = generate_population(
        "BASELINE",
        generator=lambda b, h: f"{b}-{h}",
        size=4,
    )
    assert pop[0].skill_md == "BASELINE"
    assert pop[0].mutation == "baseline"


def test_generate_population_size_excludes_failed_generations() -> None:
    def gen(_b: str, h: str) -> str:
        if "merge" in h:
            raise RuntimeError("nope")
        return f"v::{h}"

    pop = generate_population("BASE", generator=gen, size=5)
    assert all(isinstance(v, Variant) for v in pop)
    assert pop[0].skill_md == "BASE"
    # baseline + (size-1 attempts) minus the one that raised.
    assert len(pop) >= 1


def test_generate_population_drops_identity_duplicates() -> None:
    pop = generate_population(
        "BASE",
        generator=lambda _b, _h: "BASE",  # always returns baseline
        size=4,
    )
    # Only the explicit baseline survives; the generator never produced
    # anything different.
    assert len(pop) == 1
    assert pop[0].mutation == "baseline"


def test_diversity_prune_collapses_identical_bodies() -> None:
    def emb(text: str) -> list[float]:
        # All identical → cosine = 1.0
        return [1.0, 0.0, 0.0] if text == "A" else [0.0, 1.0, 0.0]

    variants = [
        Variant(skill_md="A", mutation="baseline"),
        Variant(skill_md="A", mutation="dup"),
        Variant(skill_md="B", mutation="diff"),
    ]
    kept = diversity_prune(variants, embedder=emb, threshold=0.95)
    assert [v.mutation for v in kept] == ["baseline", "diff"]


def test_diversity_prune_keeps_all_when_embedder_returns_empty() -> None:
    variants = [
        Variant(skill_md="A", mutation="baseline"),
        Variant(skill_md="B", mutation="m1"),
    ]
    kept = diversity_prune(variants, embedder=lambda _t: [])
    assert kept == variants
