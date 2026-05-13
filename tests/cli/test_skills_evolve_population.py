"""Phase 45: ``litehorse skills evolve --population`` writes a proposal.

Phase acceptance gate: the GEPA loop runs end-to-end against a
fixture-backed eval set and drops a proposal bundle under the active
agent's ``skills/.proposals/<slug>/`` directory in less than two
minutes.
"""
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from lite_horse.cli.commands.agent import agent_home, current_agent
from lite_horse.cli.commands.skills import app
from lite_horse.cli.exit_codes import ExitCode


def _write_baseline(slug: str = "demo") -> Path:
    skills_dir = agent_home(current_agent()) / "skills" / slug
    skills_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skills_dir / "SKILL.md"
    skill_md.write_text(
        f"""---
name: {slug}
version: 1
description: phase 45 baseline
---

# {slug}

baseline body
""",
        encoding="utf-8",
    )
    return skill_md


def _write_fixture(path: Path, n: int = 4) -> Path:
    rows = [
        {
            "turn_id": f"t-{i}",
            "session_id": f"s-{i}",
            "user_request": f"request {i}",
            "rating": 1 if i % 2 == 0 else -1,
            "reason": "good" if i % 2 == 0 else "bad",
        }
        for i in range(n)
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def test_evolve_population_writes_proposal(
    litehorse_home: Path, tmp_path: Path
) -> None:
    slug = "demo"
    _write_baseline(slug)
    fixture = _write_fixture(tmp_path / "cases.json")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evolve",
            slug,
            "--population",
            "--generations",
            "1",
            "--population-size",
            "3",
            "--fixture",
            str(fixture),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output

    prop_root = (
        agent_home(current_agent()) / "skills" / ".proposals" / slug
    )
    md_files = list(prop_root.glob("*.md"))
    side_files = list(prop_root.glob("*.json"))
    assert len(md_files) == 1
    assert len(side_files) == 1
    side = json.loads(side_files[0].read_text(encoding="utf-8"))
    assert side["source"] == "gepa"
    assert side["skill"] == slug


def test_evolve_population_requires_fixture(litehorse_home: Path) -> None:
    _write_baseline("demo")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["evolve", "demo", "--population", "--generations", "1"],
    )
    assert result.exit_code == int(ExitCode.USAGE)
    assert "fixture" in result.output.lower()


def test_evolve_population_rejects_missing_baseline(
    litehorse_home: Path, tmp_path: Path
) -> None:
    fixture = _write_fixture(tmp_path / "cases.json")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evolve",
            "ghost",
            "--population",
            "--fixture",
            str(fixture),
        ],
    )
    assert result.exit_code == int(ExitCode.USAGE)
    assert "SKILL.md" in result.output


def test_evolve_population_aborts_under_min_cases(
    litehorse_home: Path, tmp_path: Path
) -> None:
    slug = "demo"
    _write_baseline(slug)
    # Empty fixture → mine_eval_set_from_outcomes returns 0 cases.
    fixture = tmp_path / "cases.json"
    fixture.write_text("[]", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "evolve",
            slug,
            "--population",
            "--fixture",
            str(fixture),
            "--json",
        ],
    )
    # Pre-flight abort returns generic non-zero exit; the result payload
    # surfaces the aborted_reason.
    assert result.exit_code == int(ExitCode.GENERIC)
    assert "eval cases" in result.output
