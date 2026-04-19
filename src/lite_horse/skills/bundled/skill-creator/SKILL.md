---
name: skill-creator
description: Meta-skill for authoring new skills. Load before calling skill_manage create/edit so the SKILL.md you produce has correct frontmatter, sharp triggering, and progressive disclosure.
---

# skill-creator

A skill is a short markdown document that teaches you a reusable procedure.
The agent loads SKILL.md on demand when its description matches the task at
hand. Good skills earn their keep; sloppy skills add noise.

## Frontmatter (required)

Every SKILL.md begins with YAML frontmatter:

```
---
name: <slug>                # lowercase, dashes/underscores, max 64 chars
description: <one paragraph> # explicit triggering signal — when to load this
---
```

The `description` is the only thing the agent sees during discovery. Make it
state plainly: *what the skill is for* and *when to use it*. Avoid marketing
language. A reader skimming a list of 30 skills should be able to tell in one
sentence whether yours applies.

## Body structure

Keep the body short. Aim for under 200 lines. Three sections cover most cases:

1. **When to use** — concrete triggers. List situations, not feelings.
2. **How** — a numbered procedure the agent can follow.
3. **Anti-patterns** — common ways the procedure gets corrupted in practice.

If the skill needs supporting material (templates, scripts, reference data),
put it in sibling files inside the skill directory and link to it from the
body. Don't inline 500 lines of reference text — that defeats progressive
disclosure.

## When to write a skill

After finishing a task, ask:
- Did this take ≥ 5 tool calls?
- Will I (or the user) likely encounter the same shape of task again?
- Is the procedure non-obvious — something I'd benefit from re-reading?

If yes to all three, write a skill. Otherwise, don't.

## When NOT to write a skill

- One-off tasks.
- Procedures already covered by an existing skill (extend that one instead).
- Notes that belong in `memory` (facts, preferences) rather than procedures.
