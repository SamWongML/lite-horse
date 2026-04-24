# `litehorse` CLI

The `litehorse` command is the interactive-first face of the lite-horse
runtime. Bare `litehorse` drops into a streaming REPL; named subcommands
(`sessions`, `skills`, `cron`, `memory`, `logs`, …) are the scripted
surface that CI, cron, and automation use.

Both surfaces share the same on-disk state under `~/.litehorse/`
(override with `LITEHORSE_HOME`). They never run in the same process —
the CLI is independent of any webapp that embeds `lite_horse.api`.

## Invocation

```
litehorse                       # REPL (fresh session)
litehorse "write a haiku"       # one-shot; stream response, exit
echo "hi" | litehorse           # one-shot from piped stdin
litehorse --session <key>       # REPL bound to an existing session
```

`litehorse --help` is the canonical surface reference and returns in
<200 ms (no `openai` / `prompt_toolkit` / `rich` loaded on the help
fast-path).

## REPL

### Key bindings

| Key | Action |
|---|---|
| Enter | Insert newline (multiline editor) |
| Meta-Enter / Esc Enter | Submit |
| Ctrl-C | 1st: cancel turn · 2nd within 2 s: exit |
| Ctrl-D | Exit (empty input only) |
| Ctrl-L | Clear scrollback |
| Ctrl-R | Reverse-incremental history search |
| Ctrl-X Ctrl-E | Edit buffer in `$EDITOR` |
| Ctrl-O | Toggle tool-call expansion |
| Tab | Completion (slash commands, paths after `@`) |

### Bottom toolbar

Per-keystroke status line:

```
<model>  session:<key[:8]>  ctx:<used>/<max> (pct%)  $<cost>  [<mode>]
```

### Slash commands

```
/help, /h                       command reference
/exit, /quit, /q                exit
/clear, /cls                    clear scrollback (same as Ctrl-L)
/new                            new session (preserves model + permission)
/resume [<key>]                 pick session or resume by key prefix
/fork                           branch current session to a new key
/compact                        compression-as-consolidation now
/share                          export current session → debug bundle
/model [<name>]                 show / switch model
/permission [auto|ask|ro]       show / switch permission mode
/debug [on|off]                 toggle DEBUG-level logging
/verbose [off|new|all]          tool-call display level
/usage, /cost                   token + cost meter
/skills                         list activatable skills
/skill <slug>                   hint-activate a skill for the next turn
/cron [list|add|enable|disable] cron CRUD without leaving REPL
/memory [show|clear]            memory inspection
/attach <path|url>              add file or URL to next turn
/paste-image                    attach clipboard image (vision models)
/abort                          cancel current turn (same as Ctrl-C #1)
/logs [N]                       tail stderr log in a pager (default 50)
/config [<key>=<value>]         show or patch config
/editor                         compose next prompt in $EDITOR
```

### Permission modes

- `auto` — every tool the agent carries is offered to the model.
- `ask` — tool calls are surfaced verbosely for human review; destructive
  tools prompt y/n/A/N before running.
- `ro` — write tools (`memory`, `skill_manage`, `cron_manage`) are
  filtered out at agent-build time.

Scope: per-session. `/permission ro` persists until you switch back or
close the REPL. The scoped allow / deny lists survive turn boundaries.

### Attachments

- `@path/to/file.md` anywhere in a prompt stages the file for the next
  turn (deduplicated against prior `/attach`).
- `@https://…` stages a URL.
- `/attach <path-or-url>` stages explicitly (useful when the token would
  otherwise be quoted).
- `/paste-image` grabs the clipboard bitmap (macOS / Wayland / Windows
  PowerShell) and attaches it as a base64 image part. Only effective
  with vision-capable models.

## Scripted subcommands

Every subcommand accepts `--help`. Those producing structured output
(`sessions list`, `skills list`, `logs tail`, `debug share`, …) accept
`--json` for one NDJSON record per line on stdout.

### `sessions`

```
litehorse sessions list [--limit N] [--json]
litehorse sessions show <key> [--json]
litehorse sessions search "<query>" [--limit N] [--source S] [--json]
litehorse sessions end <key> [--reason R] [--json]
litehorse sessions cleanup [--days N] [-y] [--json]
```

### `skills`

```
litehorse skills list [--json]
litehorse skills show <slug> [--json]
litehorse skills evolve <slug> [--days N] [--approve]   # runs offline evolve
litehorse skills proposals list [<slug>] [--json]
litehorse skills proposals show <path>
litehorse skills proposals approve <path>               # re-runs gates
litehorse skills proposals reject <path>
```

Auto-merge is never supported. `approve` re-runs every
`lite_horse.evolve.constraints` gate against the candidate before copy.

### `cron`

```
litehorse cron list [--json]
litehorse cron add "<schedule>" "<prompt>" [--delivery log|webhook]
litehorse cron enable <id>
litehorse cron disable <id>
litehorse cron remove <id>
litehorse cron run-once <id>
litehorse cron scheduler                   # blocks; replaces run_scheduler_blocking
```

SIGTERM to `scheduler` triggers a clean shutdown within 3 s.

### `memory`, `config`, `logs`, `doctor`, `debug`

```
litehorse memory {show,clear} [--user]
litehorse config {show,path,edit}
litehorse logs {tail [-n N] [-f], path}
litehorse doctor                           # env + DB + OpenAI + MCP
litehorse debug share [--session K] [-n N]   # bundle log+transcript+config
```

`doctor` is the single-command health check. `debug share` writes a
plain-text bundle (config + session transcript + log tail, with API
keys and `*_SECRET` / `*_TOKEN` values redacted) to a temp file and
prints its path. Nothing is uploaded by default.

### `version`, `completion`

```
litehorse version
litehorse completion install {bash|zsh|fish}
```

## Logging

- Plain-text rotating log at `~/.litehorse/litehorse.log` (path also
  printed by `litehorse logs path`).
- Human mode: colorized on stderr via `rich.logging.RichHandler`.
- Structured mode: one JSON object per line on stderr. Activate via
  `LITEHORSE_STRUCTURED_LOGS=1` or pass `--json` on any subcommand.

## Exit codes

| Code | Meaning |
|---|---|
| 0   | OK |
| 1   | Generic error |
| 2   | Usage error (Click default) |
| 3   | Config error |
| 4   | Auth error (e.g. no `OPENAI_API_KEY`) |
| 5   | Not found (session / skill / proposal) |
| 6   | Conflict (concurrent session lock) |
| 7   | I/O error (disk full, DB locked) |
| 130 | SIGINT (one-shot only — REPL handles Ctrl-C in-loop) |
| 143 | SIGTERM |

## Environment

Flag > `LITEHORSE_*` env > `~/.litehorse/config.yaml` > defaults.

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Passed to the OpenAI SDK. Required. |
| `LITEHORSE_HOME` | Override for `~/.litehorse`. |
| `LITEHORSE_DEBUG` | `1` → DEBUG-level root logger from process start. |
| `LITEHORSE_STRUCTURED_LOGS` | `1` → JSON-lines stderr logging. |
| `LITEHORSE_WEBHOOK_SECRET` | Required by the cron worker for webhook delivery. |

## Relationship to the embedded API

The CLI is a consumer of the same internals that `lite_horse.api` drives
for a webapp — they share `~/.litehorse/` state but do not run in the
same process. Embedding a webapp? Keep importing `lite_horse.api`; the
CLI does not replace it. See [`docs/EMBEDDING.md`](EMBEDDING.md).
