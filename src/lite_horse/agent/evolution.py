"""Post-run evolution hook: skill creation + in-use refinement.

Attached to the *main* user-facing agent via ``agent.hooks`` so it does not
recurse into the distiller / refiner side-agents. Failures are swallowed —
evolution must never break the user's run.

Two side-agents, one hook:

- **Distiller**: on a non-trivial *successful* trajectory, decide
  whether to materialise a new SKILL.md via ``skill_manage(action='create')``.
- **Refiner**: on a *failed* trajectory that viewed an existing skill,
  propose ONE ``skill_manage(action='patch')`` call against that skill
  so a future run avoids the same failure mode.

Only one of the two fires per run. Failure with a viewed skill takes
precedence over the creation path — distilling a new skill from a known-bad
trajectory is the wrong signal.

Skill reads + counter writes route through ``ctx.context.skill`` (a
:class:`SkillBackend`) so a side-agent firing on an ECS task always
lands writes against the right tenant. The same :class:`TenantContext`
is propagated into the distiller / refiner runs via
``Runner.run(..., context=tenant)`` so their tool calls see the same
backends.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper, Runner, Tool

from lite_horse.agent.backends import resolve_tenant
from lite_horse.constants import (
    CURATOR_REFINE_MIN_OUTCOMES,
    SKILL_CREATION_MIN_TOOL_CALLS,
)
from lite_horse.skills.manage_tool import skill_manage

_DISTILLER_INSTRUCTIONS = (
    "You inspect a completed agent trajectory and decide whether the work "
    "warrants a reusable Skill (procedural memory). A skill is worth writing when:\n"
    "- The task was non-trivial (>= 5 tool calls)\n"
    "- The approach is generalizable, not one-off\n"
    "- A future agent encountering a similar task would benefit\n\n"
    "If yes, call the `skill_manage` tool with action='create' and a complete "
    "SKILL.md. The SKILL.md must have YAML frontmatter:\n"
    "---\nname: short-kebab-name\ndescription: one sentence (<140 chars)\n"
    "version: 1.0.0\n---\n\n"
    "Followed by sections: ## When to Use / ## Procedure / ## Pitfalls / ## Verification.\n\n"
    "If the task does NOT warrant a skill, just respond with 'no skill warranted' "
    "and stop. Be conservative — bad skills are worse than no skill."
)

_REFINER_INSTRUCTIONS = (
    "You inspect a completed agent trajectory that FAILED while using the "
    "skill below. Propose ONE targeted improvement to the SKILL.md that "
    "would have avoided the failure: a clarifying line in ## Procedure, a "
    "new entry in ## Pitfalls, or a fixed step ordering. Be conservative — "
    "if the root cause is unclear or the skill isn't at fault, respond with "
    "'no patch' and stop.\n\n"
    "If you can improve it, call `skill_manage` ONCE with action='patch', "
    "name set to the skill, and old_string / new_string forming a unique, "
    "minimal diff. Do not rewrite the whole skill; prefer a surgical patch."
)

_ERROR_MARKERS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"success"\s*:\s*false', re.IGNORECASE),
    re.compile(r"traceback \(most recent call last\)", re.IGNORECASE),
    re.compile(r"\bexception\s*:", re.IGNORECASE),
    re.compile(r"\bfailed\b", re.IGNORECASE),
)
_ERROR_SUMMARY_LEN = 300


class EvolutionHook(AgentHooks[Any]):
    """After each agent run, distill (on success) or refine (on failure)."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.4",
        min_tool_calls: int = SKILL_CREATION_MIN_TOOL_CALLS,
        distiller_max_turns: int = 4,
        refiner_max_turns: int = 4,
    ) -> None:
        self.model = model
        self.min_tool_calls = min_tool_calls
        self.distiller_max_turns = distiller_max_turns
        self.refiner_max_turns = refiner_max_turns
        self._tool_call_count: int = 0
        self._user_request: str | None = None
        self._final_output: str | None = None
        self._viewed_skills: list[str] = []
        self._error_summary: str | None = None

    async def on_start(
        self, context: RunContextWrapper[Any], agent: Agent[Any]
    ) -> None:
        del agent
        self._tool_call_count = 0
        self._final_output = None
        self._viewed_skills = []
        self._error_summary = None
        self._user_request = self._extract_user_request(context)

    async def on_tool_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        tool: Tool,
        result: str,
    ) -> None:
        del context, agent
        self._tool_call_count += 1
        tool_name = getattr(tool, "name", None) or ""
        if tool_name == "skill_view":
            self._track_view(result)
            return
        if self._viewed_skills and self._error_summary is None:
            marker = _first_error_marker(result)
            if marker is not None:
                self._error_summary = marker

    async def on_end(
        self,
        context: RunContextWrapper[Any],
        agent: Agent[Any],
        output: Any,
    ) -> None:
        del agent
        self._final_output = str(output)[:1000] if output else None
        await self._record_viewed_outcomes(context)
        should_refine = await self._should_refine(context)
        if should_refine:
            await self._maybe_refine_skill(context)
            return
        if self._tool_call_count < self.min_tool_calls:
            return
        await self._maybe_create_skill(context)

    async def _should_refine(self, context: RunContextWrapper[Any]) -> bool:
        """Decide whether to invoke the refiner side-agent.

        In-trajectory error markers remain the primary trigger. The
        feedback path adds two gates:

        * once the most-recently viewed skill has accumulated
          ``CURATOR_REFINE_MIN_OUTCOMES`` rated turns, the aggregate
          net-rating overrides the in-trajectory signal — non-negative
          history suppresses refinement, negative history triggers it
          even without a regex-error marker.
        """
        if not self._viewed_skills:
            return False
        target = self._viewed_skills[-1]
        marker_says_fail = self._error_summary is not None
        stats = await self._latest_skill_stats(context, target)
        if stats is None:
            return marker_says_fail
        outcomes = stats.success_count + stats.error_count
        if outcomes < CURATOR_REFINE_MIN_OUTCOMES:
            return marker_says_fail
        net = stats.success_count - stats.error_count
        if net >= 0:
            return False
        return True

    async def _latest_skill_stats(
        self, context: RunContextWrapper[Any], skill_slug: str
    ) -> Any:
        feedback = getattr(resolve_tenant(context), "feedback", None)
        if feedback is None:
            return None
        try:
            return await feedback.rating_stats(skill_slug=skill_slug)
        except Exception:
            return None

    def _track_view(self, result: str) -> None:
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, ValueError, TypeError):
            return
        if not isinstance(parsed, dict) or not parsed.get("success"):
            return
        name = parsed.get("name")
        if isinstance(name, str) and name not in self._viewed_skills:
            self._viewed_skills.append(name)

    async def _record_viewed_outcomes(
        self, context: RunContextWrapper[Any]
    ) -> None:
        ok = self._error_summary is None
        backend = resolve_tenant(context).skill
        for name in self._viewed_skills:
            try:
                await backend.record_outcome(
                    name, ok=ok, error_summary=self._error_summary
                )
            except Exception:
                continue

    async def _maybe_create_skill(
        self, context: RunContextWrapper[Any]
    ) -> None:
        decider = Agent(
            name="skill-distiller",
            model=self.model,
            instructions=_DISTILLER_INSTRUCTIONS,
            tools=[skill_manage],
        )
        prompt = json.dumps(
            {
                "user_request": self._user_request,
                "tool_calls_used": self._tool_call_count,
                "final_output_excerpt": self._final_output,
            }
        )
        tenant = resolve_tenant(context)
        try:
            await Runner.run(
                decider,
                prompt,
                max_turns=self.distiller_max_turns,
                context=tenant,
            )
        except Exception:
            # Evolution must never break the main run.
            pass

    async def _maybe_refine_skill(
        self, context: RunContextWrapper[Any]
    ) -> None:
        """Refine the most recently viewed skill on a failed run."""
        target = self._viewed_skills[-1]
        tenant = resolve_tenant(context)
        skill_md = await tenant.skill.read_md(target)
        if not skill_md:
            return
        refiner = Agent(
            name="skill-refiner",
            model=self.model,
            instructions=_REFINER_INSTRUCTIONS,
            tools=[skill_manage],
        )
        prompt = json.dumps(
            {
                "skill_name": target,
                "skill_markdown": skill_md,
                "user_request": self._user_request,
                "error_summary": self._error_summary,
                "final_output_excerpt": self._final_output,
            }
        )
        try:
            await Runner.run(
                refiner,
                prompt,
                max_turns=self.refiner_max_turns,
                context=tenant,
            )
        except Exception:
            pass

    @staticmethod
    def _extract_user_request(context: RunContextWrapper[Any]) -> str | None:
        """Best-effort scrape of the last user message from the turn input."""
        try:
            items = getattr(context, "turn_input", None)
            if not isinstance(items, list):
                return None
            for item in reversed(items):
                if isinstance(item, dict) and item.get("role") == "user":
                    content = item.get("content")
                    if isinstance(content, str):
                        return content[:500]
                    if isinstance(content, list):
                        parts = [
                            p.get("text", "")
                            for p in content
                            if isinstance(p, dict) and p.get("type") in {"text", "input_text"}
                        ]
                        joined = "".join(parts)
                        return joined[:500] if joined else None
                elif isinstance(item, str):
                    return item[:500]
        except Exception:
            return None
        return None


def _first_error_marker(result: str) -> str | None:
    """Return a short summary if ``result`` matches any error marker."""
    if not isinstance(result, str) or not result:
        return None
    for pat in _ERROR_MARKERS:
        match = pat.search(result)
        if match:
            start = max(0, match.start() - 40)
            end = min(len(result), match.end() + 60)
            return result[start:end].strip()[:_ERROR_SUMMARY_LEN]
    return None
