"""Tests for `litehorse skills {list, show, evolve}` + `proposals {list, show, approve, reject}`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lite_horse.cli.commands.skills import app
from lite_horse.cli.exit_codes import ExitCode
from lite_horse.skills.source import skills_root


def _write_skill(home: Path, name: str, *, version: int = 1) -> Path:
    root = skills_root()
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text(
        f"""---
name: {name}
version: {version}
description: test skill
---
body for {name}
""",
        encoding="utf-8",
    )
    return skill_md


def _write_proposal(
    home: Path, name: str, *, ts: str = "20260424T000000", version: int = 2
) -> Path:
    prop_dir = skills_root() / ".proposals" / name
    prop_dir.mkdir(parents=True, exist_ok=True)
    md = prop_dir / f"{ts}.md"
    md.write_text(
        f"""---
name: {name}
version: {version}
description: evolved
---
refined body for {name}
""",
        encoding="utf-8",
    )
    sidecar = prop_dir / f"{ts}.json"
    sidecar.write_text(json.dumps({"fitness": {"total": 0.9}}), encoding="utf-8")
    return md


# ---------- list / show ----------


def test_list_shows_installed_skills(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    _write_skill(litehorse_home, "review")
    runner = CliRunner()
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "plan" in result.stdout
    assert "review" in result.stdout


def test_list_ignores_dot_proposals_dir(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    (skills_root() / ".proposals").mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    items = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    item_names = [r["data"]["name"] for r in items if r["kind"] == "item"]
    assert item_names == ["plan"]


def test_show_returns_content(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    runner = CliRunner()
    result = runner.invoke(app, ["show", "plan"])
    assert result.exit_code == 0
    assert "body for plan" in result.stdout


def test_show_missing_skill_returns_not_found(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["show", "nope"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


def test_show_rejects_bad_slug(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["show", "../etc"])
    assert result.exit_code == int(ExitCode.NOT_FOUND)


# ---------- proposals list / show ----------


def test_proposals_list_walks_tree(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    _write_proposal(litehorse_home, "plan", ts="20260424T000000")
    _write_proposal(litehorse_home, "plan", ts="20260424T010000")
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "list", "--json"])
    assert result.exit_code == 0
    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    items = [r for r in lines if r["kind"] == "item"]
    assert len(items) == 2


def test_proposals_list_filters_by_slug(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    _write_skill(litehorse_home, "review")
    _write_proposal(litehorse_home, "plan")
    _write_proposal(litehorse_home, "review", ts="20260424T020000")
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "list", "plan", "--json"])
    items = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip()
    ]
    item_skills = {r["data"]["skill"] for r in items if r["kind"] == "item"}
    assert item_skills == {"plan"}


def test_proposals_show_parses_sidecar(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    md = _write_proposal(litehorse_home, "plan")
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "show", str(md), "--json"])
    assert result.exit_code == 0
    record = json.loads(result.stdout.splitlines()[0])
    assert record["data"]["skill"] == "plan"
    assert record["data"]["sidecar"]["fitness"]["total"] == 0.9


# ---------- path-escape guard ----------


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/passwd",
        "../../etc/passwd",
    ],
)
def test_proposals_approve_rejects_path_escape(
    litehorse_home: Path, bad_path: str
) -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "approve", bad_path])
    assert result.exit_code == int(ExitCode.USAGE), result.stdout


def test_proposals_reject_rejects_path_escape(litehorse_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["proposals", "reject", "../../etc/passwd"]
    )
    assert result.exit_code == int(ExitCode.USAGE)


# ---------- approve / reject ----------


def test_proposals_approve_reruns_gates_and_swaps(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_skill(litehorse_home, "plan", version=1)
    md = _write_proposal(litehorse_home, "plan", version=2)

    # Short-circuit the pytest gate so the approval works offline.
    monkeypatch.setattr(
        "lite_horse.evolve.constraints._default_pytest_runner",
        lambda: True,
        raising=True,
    )

    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "approve", str(md)])
    assert result.exit_code == 0, result.stdout
    live_md = skills_root() / "plan" / "SKILL.md"
    assert "refined body for plan" in live_md.read_text(encoding="utf-8")
    assert not md.exists()
    assert not md.with_suffix(".json").exists()


def test_proposals_approve_rejected_by_frontmatter_gate(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_skill(litehorse_home, "plan", version=5)
    # Candidate does not bump version → frontmatter gate should fail.
    md = _write_proposal(litehorse_home, "plan", version=1)

    monkeypatch.setattr(
        "lite_horse.evolve.constraints._default_pytest_runner",
        lambda: True,
        raising=True,
    )
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "approve", str(md)])
    assert result.exit_code == int(ExitCode.CONFLICT)
    assert md.exists()  # not swapped
    live = skills_root() / "plan" / "SKILL.md"
    assert "body for plan" in live.read_text(encoding="utf-8")  # untouched


def test_proposals_reject_deletes_bundle(litehorse_home: Path) -> None:
    _write_skill(litehorse_home, "plan")
    md = _write_proposal(litehorse_home, "plan")
    runner = CliRunner()
    result = runner.invoke(app, ["proposals", "reject", str(md)])
    assert result.exit_code == 0
    assert not md.exists()


# ---------- evolve (with stubbed runner) ----------


def test_evolve_invokes_runner_and_emits_result(
    litehorse_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_skill(litehorse_home, "plan")

    from lite_horse.evolve.runner import EvolveResult

    def _fake_evolve(name: str, *, days: int = 14, **_: Any) -> EvolveResult:
        return EvolveResult(
            skill_name=name,
            approved=False,
            gates={},
            fitness=None,
            proposal_path=None,
            sidecar_path=None,
            reason="stubbed",
        )

    monkeypatch.setattr("lite_horse.cli.commands.skills.evolve_fn", _fake_evolve,
                        raising=False)
    # `evolve_cmd` imports via `from ... import evolve as evolve_fn`, so the
    # binding inside the function's closure is what we need to patch.
    import lite_horse.evolve.runner as runner_mod

    monkeypatch.setattr(runner_mod, "evolve", _fake_evolve, raising=True)

    runner = CliRunner()
    result = runner.invoke(app, ["evolve", "plan", "--json"])
    # approved=False → exit GENERIC (1)
    assert result.exit_code == int(ExitCode.GENERIC)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    record = json.loads(lines[-1])
    assert record["data"]["skill_name"] == "plan"
    assert record["data"]["approved"] is False
