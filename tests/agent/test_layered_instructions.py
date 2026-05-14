""":func:`build_layered_instructions` splits the prompt for caching."""
from __future__ import annotations

from lite_horse.agent.instructions import (
    LayeredInstructions,
    build_layered_instructions,
)


def test_stable_layer_carries_instructions_and_tool_guidance() -> None:
    layered = build_layered_instructions(
        instruction_blocks=["INSTR_A"],
        profile_block="PROFILE",
        memory_block="MEM",
        recent_block="",
        relevant_block="",
        skills_index="SKILLS",
        tool_guidance="TOOLS",
        now_iso="2026-05-14",
    )
    assert isinstance(layered, LayeredInstructions)
    assert "INSTR_A" in layered.stable
    assert "TOOLS" in layered.stable
    # semi-stable carries profile + memory + skills
    assert "PROFILE" in layered.semi_stable
    assert "MEM" in layered.semi_stable
    assert "SKILLS" in layered.semi_stable
    # volatile carries the timestamp
    assert "Current time: 2026-05-14" in layered.volatile


def test_as_text_orders_stable_then_semi_then_volatile() -> None:
    layered = build_layered_instructions(
        instruction_blocks=["I"],
        profile_block="P",
        memory_block="M",
        recent_block="R",
        relevant_block="V",
        skills_index="S",
        tool_guidance="TG",
        now_iso="T",
    )
    text = layered.as_text()
    assert text.index("I") < text.index("P")
    assert text.index("P") < text.index("R")
    assert text.index("R") < text.index("Current time:")


def test_empty_semi_stable_layer_drops_from_as_text() -> None:
    layered = build_layered_instructions(
        instruction_blocks=["I"],
        profile_block="",
        memory_block="",
        recent_block="",
        relevant_block="",
        skills_index="",
        tool_guidance="TG",
        now_iso="T",
    )
    assert layered.semi_stable == ""
    text = layered.as_text()
    # No stray empty paragraph between stable and volatile.
    assert "\n\n\n" not in text
