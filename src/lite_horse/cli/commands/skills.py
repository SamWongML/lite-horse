"""`litehorse skills {list, show, evolve}` + `skills proposals {list, show, approve, reject}`.

`evolve` delegates to :func:`lite_horse.evolve.runner.evolve`. The ``proposals``
subgroup operates on ``~/.litehorse/skills/.proposals/<slug>/<ts>.md`` bundles
produced by that runner. `approve` re-runs every constraint gate before
copying the candidate onto the live SKILL.md — never blind-trusts the earlier
pass, and rejects any path that escapes the proposals root.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

app = typer.Typer(
    help="Inspect and evolve skills.",
    no_args_is_help=True,
)

proposals_app = typer.Typer(
    help="Review evolve proposals produced by `litehorse skills evolve`.",
    no_args_is_help=True,
)
app.add_typer(proposals_app, name="proposals")


@app.callback()
def _root() -> None:
    """Group callback so typer emits a subcommand-ready Click group."""


@proposals_app.callback()
def _proposals_callback() -> None:
    """Group callback for the proposals subgroup."""


# ---------- pure helpers (shared with slash handlers) ----------

_PROPOSALS_DIRNAME = ".proposals"


def _skills_root_resolved() -> Path:
    from lite_horse.skills.source import skills_root

    return skills_root().resolve()


def _proposals_root() -> Path:
    return _skills_root_resolved() / _PROPOSALS_DIRNAME


def list_skills() -> list[dict[str, Any]]:
    """Each on-disk skill with its frontmatter description (if any)."""
    import yaml

    out: list[dict[str, Any]] = []
    root = _skills_root_resolved()
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        skill_md = d / "SKILL.md"
        description: str | None = None
        version: int | None = None
        if skill_md.is_file():
            text = skill_md.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("\n---", 3)
                if end != -1:
                    try:
                        fm = yaml.safe_load(text[3:end])
                    except yaml.YAMLError:
                        fm = None
                    if isinstance(fm, dict):
                        description = (
                            str(fm.get("description")) if fm.get("description") else None
                        )
                        if isinstance(fm.get("version"), int):
                            version = int(fm["version"])
        out.append({
            "name": d.name,
            "path": str(skill_md),
            "description": description,
            "version": version,
        })
    return out


def show_skill(slug: str) -> dict[str, Any] | None:
    from lite_horse.skills._slug import _SLUG_RE

    if not _SLUG_RE.match(slug):
        return None
    skill_md = _skills_root_resolved() / slug / "SKILL.md"
    if not skill_md.is_file():
        return None
    return {
        "name": slug,
        "path": str(skill_md),
        "content": skill_md.read_text(encoding="utf-8"),
    }


def list_proposals(slug: str | None = None) -> list[dict[str, Any]]:
    """Every pending proposal, optionally filtered to one skill."""
    root = _proposals_root()
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        if slug is not None and sub.name != slug:
            continue
        for md in sorted(sub.glob("*.md")):
            sidecar = md.with_suffix(".json")
            out.append({
                "skill": sub.name,
                "path": str(md),
                "sidecar": str(sidecar) if sidecar.exists() else None,
            })
    return out


def _resolve_proposal(path_str: str) -> tuple[Path, str]:
    """Resolve a user-provided path; raise ValueError if outside proposals root.

    Returns ``(resolved_md_path, skill_slug)``.
    """
    from lite_horse.skills._slug import _SLUG_RE

    root = _proposals_root().resolve()
    candidate = Path(path_str).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"path escapes proposals root: {path_str!r}")
    if candidate.suffix != ".md":
        raise ValueError("proposal path must end in .md")
    if not candidate.is_file():
        raise ValueError(f"no such proposal: {candidate}")
    # skill slug is the parent directory under proposals root.
    try:
        rel = candidate.relative_to(root)
    except ValueError as exc:  # pragma: no cover - guarded above
        raise ValueError(str(exc)) from exc
    if len(rel.parts) != 2:
        raise ValueError("proposal must live at .proposals/<slug>/<ts>.md")
    slug = rel.parts[0]
    if not _SLUG_RE.match(slug):
        raise ValueError(f"invalid skill slug in path: {slug!r}")
    return candidate, slug


def show_proposal(path_str: str) -> dict[str, Any]:
    import json

    md, slug = _resolve_proposal(path_str)
    sidecar = md.with_suffix(".json")
    out: dict[str, Any] = {
        "skill": slug,
        "path": str(md),
        "content": md.read_text(encoding="utf-8"),
    }
    if sidecar.is_file():
        try:
            out["sidecar"] = json.loads(sidecar.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out["sidecar"] = None
    return out


def approve_proposal(path_str: str, *, pytest_runner: Any = None) -> dict[str, Any]:
    """Re-run every constraint gate, then swap the candidate into SKILL.md.

    Gates re-evaluated: size, injection, frontmatter. The cosine gate stays
    the evolve runner's responsibility (it needs an embedder); approve trusts
    the sidecar-recorded cosine since the candidate's content is already on
    disk and a second embedding call costs money. ``pytest_runner`` is
    injectable so the test suite can re-use the shared stub.
    """
    import yaml

    from lite_horse.evolve import constraints
    from lite_horse.skills import stats as skills_stats

    md, slug = _resolve_proposal(path_str)
    candidate = md.read_text(encoding="utf-8")

    live = _skills_root_resolved() / slug / "SKILL.md"
    if not live.is_file():
        return {
            "success": False,
            "error": f"live skill {slug!r} missing; cannot approve against baseline",
        }
    baseline = live.read_text(encoding="utf-8")
    baseline_name = slug
    baseline_version: int | None = None
    if baseline.startswith("---"):
        end = baseline.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(baseline[3:end])
            except yaml.YAMLError:
                fm = {}
            if isinstance(fm, dict):
                baseline_name = str(fm.get("name") or slug)
                if isinstance(fm.get("version"), int):
                    baseline_version = int(fm["version"])

    gates = {
        "size": constraints.check_size(candidate),
        "injection": constraints.check_injection(candidate),
        "frontmatter": constraints.check_frontmatter(
            candidate,
            baseline_name=baseline_name,
            baseline_version=baseline_version,
        ),
        "pytest": constraints.check_pytest(pytest_runner),
    }
    failing = [g.name for g in gates.values() if not g.passed]
    if failing:
        return {
            "success": False,
            "error": f"gates failed: {','.join(failing)}",
            "gates": {
                k: {"name": g.name, "passed": g.passed, "reason": g.reason}
                for k, g in gates.items()
            },
        }

    live.write_text(candidate, encoding="utf-8")
    skills_stats.mark_optimized(live.parent)
    sidecar = md.with_suffix(".json")
    md.unlink()
    if sidecar.is_file():
        sidecar.unlink()
    return {
        "success": True,
        "skill": slug,
        "live_path": str(live),
    }


def reject_proposal(path_str: str) -> dict[str, Any]:
    md, slug = _resolve_proposal(path_str)
    sidecar = md.with_suffix(".json")
    md.unlink()
    if sidecar.is_file():
        sidecar.unlink()
    return {"success": True, "skill": slug, "rejected": str(md)}


# ---------- Typer commands ----------

@app.command("list")
def list_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Show every installed skill with its description."""
    from lite_horse.cli._output import emit_item, emit_result

    rows = list_skills()
    for r in rows:
        emit_item(r, json_mode=json_mode)
    emit_result({"count": len(rows)} if json_mode else f"{len(rows)} skills",
                json_mode=json_mode)


@app.command("show")
def show_cmd(
    slug: str = typer.Argument(..., help="Skill directory name."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print a skill's SKILL.md."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    data = show_skill(slug)
    if data is None:
        emit_error(f"no skill {slug!r}", code=int(ExitCode.NOT_FOUND), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.NOT_FOUND))
    if json_mode:
        emit_result(data, json_mode=True)
    else:
        emit_result(data["content"], json_mode=False)


@app.command("evolve")
def evolve_cmd(
    slug: str = typer.Argument(..., help="Skill slug to evolve."),
    days: int = typer.Option(14, "--days", help="Trace-mining window."),
    approve: bool = typer.Option(
        False, "--approve",
        help="Immediately approve the proposal if every gate passes. "
             "Defaults off — auto-merge is forbidden by the v0.2 contract.",
    ),
    population: bool = typer.Option(
        False, "--population",
        help="Phase 45 GEPA mode: evolve via an N-generation population "
             "loop and emit a proposal under ``.proposals/<slug>/``. "
             "Requires --fixture <path.json>.",
    ),
    generations: int = typer.Option(
        3, "--generations", help="GEPA generations (--population only)."
    ),
    population_size: int = typer.Option(
        8, "--population-size", help="Variants per generation (--population only)."
    ),
    fixture: str = typer.Option(
        None, "--fixture",
        help="Path to a JSON array of EvalCase dicts for --population.",
    ),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Mine recent failures, propose a revised SKILL.md, gate it."""
    from dataclasses import asdict

    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    if population:
        try:
            payload = _run_gepa_local(
                slug=slug,
                fixture=fixture,
                generations=generations,
                population_size=population_size,
            )
        except ValueError as exc:
            emit_error(str(exc), code=int(ExitCode.USAGE), json_mode=json_mode)
            raise typer.Exit(code=int(ExitCode.USAGE)) from None
        emit_result(payload, json_mode=json_mode)
        if not payload.get("accepted"):
            raise typer.Exit(code=int(ExitCode.GENERIC))
        return

    from lite_horse.evolve.runner import evolve as evolve_fn

    result = evolve_fn(slug, days=days)
    payload = asdict(result)

    if approve and result.approved and result.proposal_path:
        approval = approve_proposal(result.proposal_path)
        payload["approved_merge"] = approval
        if not approval.get("success"):
            emit_error(
                f"gates re-ran and failed: {approval.get('error')}",
                code=int(ExitCode.CONFLICT), json_mode=json_mode,
            )
            emit_result(payload, json_mode=json_mode)
            raise typer.Exit(code=int(ExitCode.CONFLICT))

    emit_result(payload, json_mode=json_mode)
    if not result.approved:
        raise typer.Exit(code=int(ExitCode.GENERIC))


def _run_gepa_local(
    *,
    slug: str,
    fixture: str | None,
    generations: int,
    population_size: int,
) -> dict[str, Any]:
    """Phase 45 GEPA local entry-point. Returns a JSON-safe summary dict.

    Reads the baseline SKILL.md from the current agent's skills tree,
    loads eval cases from ``--fixture`` JSON, runs the deterministic
    local GEPA loop, and (on success) drops a proposal bundle under
    ``<agent>/skills/.proposals/<slug>/<ts>.{md,json}``. The summary
    mirrors :class:`GepaResult` plus the on-disk paths so callers can
    print or assert against it.
    """
    import json
    from dataclasses import asdict

    from lite_horse.cli.commands.agent import agent_home, current_agent
    from lite_horse.evolve.gepa.eval_set import mine_eval_set_from_outcomes
    from lite_horse.evolve.gepa.local import (
        local_classifier,
        local_embedder,
        local_generator,
        local_run_case,
    )
    from lite_horse.evolve.gepa.runner import (
        run_gepa,
        write_local_proposal,
    )
    from lite_horse.skills._slug import _SLUG_RE

    if not _SLUG_RE.match(slug):
        raise ValueError(f"invalid skill slug: {slug!r}")
    if not fixture:
        raise ValueError(
            "--population requires --fixture <path.json> "
            "(a JSON array of EvalCase dicts)"
        )

    agent_slug = current_agent()
    agent_skills_root = agent_home(agent_slug) / "skills"
    baseline_path = agent_skills_root / slug / "SKILL.md"
    if not baseline_path.is_file():
        raise ValueError(f"no live SKILL.md for {slug!r} at {baseline_path}")
    baseline = baseline_path.read_text(encoding="utf-8")

    fixture_path = Path(fixture)
    if not fixture_path.is_file():
        raise ValueError(f"fixture not found: {fixture}")
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"fixture is not valid JSON: {exc}") from None
    if not isinstance(raw, list):
        raise ValueError("fixture must be a JSON array of EvalCase dicts")
    cases = mine_eval_set_from_outcomes(raw, min_cases=1, max_cases=len(raw))

    result = run_gepa(
        skill_slug=slug,
        baseline=baseline,
        cases=cases,
        generator=local_generator(),
        embedder=local_embedder(),
        run_case=local_run_case(),
        classifier=local_classifier(),
        population_size=population_size,
        generations=generations,
        min_cases=1,
    )

    payload: dict[str, Any] = asdict(result)
    payload["agent"] = agent_slug
    written = write_local_proposal(
        skill_slug=slug,
        result=result,
        agent_skills_root=agent_skills_root,
    )
    if written:
        prop_md, side_json = written
        payload["proposal_path"] = str(prop_md)
        payload["proposal_sidecar"] = str(side_json)
    else:
        payload["proposal_path"] = None
        payload["proposal_sidecar"] = None
    return payload


@app.command("curate")
def curate_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
    stale_days: int = typer.Option(
        None, "--stale-days",
        help="Override curator stale threshold (default: 30).",
    ),
    archive_days: int = typer.Option(
        None, "--archive-days",
        help="Override curator archive threshold (default: 90).",
    ),
) -> None:
    """Report which skills would transition under the curator's rules.

    Read-only by design: prints a per-skill state report based on the
    skill directory's most-recent modification time and recorded
    successes in ``~/.litehorse/feedback.log``. Mirrors the cloud
    curator's transition rules so a user can preview before opting in.
    """
    from datetime import UTC, datetime

    from lite_horse.cli._output import emit_item, emit_result
    from lite_horse.constants import (
        CURATOR_ARCHIVE_AFTER_DAYS,
        CURATOR_STALE_AFTER_DAYS,
    )

    stale_cutoff = stale_days if stale_days is not None else CURATOR_STALE_AFTER_DAYS
    archive_cutoff = (
        archive_days if archive_days is not None else CURATOR_ARCHIVE_AFTER_DAYS
    )
    now = datetime.now(UTC)
    feedback = _load_feedback_success_map()
    rows: list[dict[str, Any]] = []
    for s in list_skills():
        path = Path(s["path"])
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        idle_days = int((now - mtime).total_seconds() // 86400)
        successes = feedback.get(s["name"], 0)
        state = "active"
        if idle_days >= archive_cutoff and successes == 0:
            state = "archived"
        elif idle_days >= stale_cutoff:
            state = "stale"
        rows.append({
            "name": s["name"],
            "idle_days": idle_days,
            "success_count": successes,
            "would_become": state,
        })
    for r in rows:
        emit_item(r, json_mode=json_mode)
    summary = {
        "active": sum(1 for r in rows if r["would_become"] == "active"),
        "stale": sum(1 for r in rows if r["would_become"] == "stale"),
        "archived": sum(1 for r in rows if r["would_become"] == "archived"),
    }
    emit_result(
        summary if json_mode else
        f"active={summary['active']} stale={summary['stale']} "
        f"archived={summary['archived']}",
        json_mode=json_mode,
    )


def _load_feedback_success_map() -> dict[str, int]:
    """Count rating=+1 outcomes per ``skill_slug`` in the local feedback log."""
    import json as _json

    from lite_horse.constants import litehorse_home

    log_path = litehorse_home() / "feedback.log"
    counts: dict[str, int] = {}
    if not log_path.exists():
        return counts
    try:
        with log_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                slug = data.get("skill_slug")
                if not slug or int(data.get("rating") or 0) != 1:
                    continue
                counts[slug] = counts.get(slug, 0) + 1
    except OSError:
        return counts
    return counts


@proposals_app.command("list")
def proposals_list_cmd(
    slug: str = typer.Argument(None, help="Filter to one skill slug."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Show every pending proposal bundle."""
    from lite_horse.cli._output import emit_item, emit_result

    rows = list_proposals(slug)
    for r in rows:
        emit_item(r, json_mode=json_mode)
    emit_result({"count": len(rows)} if json_mode else f"{len(rows)} proposals",
                json_mode=json_mode)


@proposals_app.command("show")
def proposals_show_cmd(
    path: str = typer.Argument(..., help="Proposal .md path."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Print a proposal's candidate SKILL.md + fitness sidecar."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    try:
        data = show_proposal(path)
    except ValueError as exc:
        emit_error(str(exc), code=int(ExitCode.USAGE), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.USAGE)) from None
    emit_result(data, json_mode=json_mode)


@proposals_app.command("approve")
def proposals_approve_cmd(
    path: str = typer.Argument(..., help="Proposal .md path to approve."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Re-run gates, then copy the candidate into the live SKILL.md."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    try:
        result = approve_proposal(path)
    except ValueError as exc:
        emit_error(str(exc), code=int(ExitCode.USAGE), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.USAGE)) from None
    if not result.get("success"):
        emit_error(str(result.get("error") or "approve failed"),
                   code=int(ExitCode.CONFLICT), json_mode=json_mode)
        emit_result(result, json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.CONFLICT))
    emit_result(result, json_mode=json_mode)


@proposals_app.command("reject")
def proposals_reject_cmd(
    path: str = typer.Argument(..., help="Proposal .md path to reject."),
    json_mode: bool = typer.Option(False, "--json", help="Emit NDJSON."),
) -> None:
    """Delete the proposal (and its sidecar) without touching the live skill."""
    from lite_horse.cli._output import emit_error, emit_result
    from lite_horse.cli.exit_codes import ExitCode

    try:
        result = reject_proposal(path)
    except ValueError as exc:
        emit_error(str(exc), code=int(ExitCode.USAGE), json_mode=json_mode)
        raise typer.Exit(code=int(ExitCode.USAGE)) from None
    emit_result(result, json_mode=json_mode)
