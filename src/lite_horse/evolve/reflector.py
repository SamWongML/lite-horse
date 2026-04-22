"""Single-step LLM reflection over a skill + failure trajectories.

Returns the raw candidate text. Any explanatory comments the model emits above
the frontmatter are stripped here before validation so the constraint gates
see a clean SKILL.md.
"""
from __future__ import annotations

import re
from collections.abc import Callable

import openai

from lite_horse.evolve.trace_miner import Trajectory

Reflector = Callable[[str, list[Trajectory]], str]

_REFLECT_MODEL = "gpt-4o-mini"
_CODE_FENCE_RE = re.compile(r"```(?:markdown|md)?\n(.*?)```", re.DOTALL)


def strip_comment_preamble(text: str) -> str:
    """Drop everything before the first ``---`` fence so the gates see a valid SKILL.md."""
    idx = text.find("---")
    return text[idx:] if idx != -1 else text


def extract_skill_md(raw: str) -> str:
    """Pull the SKILL.md out of a fenced block if the model used one; strip preamble."""
    m = _CODE_FENCE_RE.search(raw)
    body = m.group(1) if m else raw
    return strip_comment_preamble(body).strip() + "\n"


def default_reflector(baseline: str, trajectories: list[Trajectory]) -> str:
    """Real LLM reflection. Replaced with a stub in tests."""
    client = openai.OpenAI()
    trace_block = "\n\n".join(
        f"TASK: {t.task}\nRESPONSE: {t.response}\nOUTCOME: {t.outcome}"
        for t in trajectories
    ) or "(no trajectories recorded)"
    system = (
        "You revise SKILL.md files for an autonomous agent. Given the baseline "
        "and 3-5 failure trajectories, propose ONE revised SKILL.md that would "
        "avoid those failures. Preserve the frontmatter `name:` exactly; bump "
        "`version:` by one. Keep the revision under 15 KB. Do not change the "
        "skill's purpose. Respond with the full revised SKILL.md inside a "
        "```markdown code fence. Above the fence, 1-3 lines of commentary on "
        "what you changed and why."
    )
    user = f"BASELINE SKILL.md:\n{baseline}\n\nFAILURES:\n{trace_block}"
    resp = client.chat.completions.create(
        model=_REFLECT_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    return extract_skill_md(resp.choices[0].message.content or "")
