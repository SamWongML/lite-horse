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

## Frontmatter (optional — conditional activation)

To keep the prompt lean as the skill library grows, a skill can declare
signals that make it eligible for that turn's top-K index:

```
---
name: deploy-helper
description: Build/push image and roll the staging deployment.
category: devops
activate_when:
  - keywords: ["deploy", "ship", "release", "rollout"]
  - file_globs: ["Dockerfile", "k8s/*.yaml", "deploy/*.sh"]
---
```

Rules:

- `activate_when` is a list of rules; each rule has `keywords` and/or
  `file_globs`. Matches are case-insensitive for keywords, case-sensitive
  for globs (filenames care about case).
- Omit `activate_when` entirely to make the skill **always-on** — it stays
  eligible every turn and fills remaining top-K slots when no specialist
  out-scores it. Core skills (`plan`, `skill-creator`) are always-on.
- `category` is a free-form label used only for grouping / reporting;
  activation itself ignores it.

Keep keywords short and concrete. A skill triggered by *everything* is
triggered by *nothing* — it becomes prompt noise.

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
