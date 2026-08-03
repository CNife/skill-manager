# Skill Manager

Project-scoped declarative skill manager for coding agents that load skills from `./.agents/skills/` (and optionally `~/.agents/skills/`).

**One source, many projects, slim globals.** Each skill lives once in a cached Source. Projects get symlinks (Links), not copies. Keep agent-global skills minimal; declare per project what you actually need.

Compatible with any agent that discovers skill directories containing `SKILL.md` under those paths ([pi](https://github.com/earendil-works/pi-coding-agent) is one example).

Project Links live under `./.agents/skills/`; user-global Links under `~/.agents/skills/` are managed with the same model via `--global` (see [Global skills](#global-skills)).

## Features

- **Declare and sync project skills** — write `.skill-manager.json`, run `skill-manager sync`
- **Inspect source and link status** — `skill-manager list` (`linked` / `external` / `broken` / `unlinked`)
- **Enable / disable** — interactive filterable picker (TTY) or non-interactive batch `enable <repo> <name>...` / `disable <name>...`
- **Manage source cache** — `skill-manager source list|add|remove|update|available-skills`
- **Global skills** - `--global` flag applies any command to user-global skills (`~/.skill-manager.json`, `~/.agents/skills/`)
- **Scripting / CI** — root `--json` on every command (`skill-manager --json <cmd> ...`)

## Install

```bash
uv tool install cnife-skill-manager
# or: pipx install cnife-skill-manager

# from source (dev):
uv tool install .
# or run in-place:
uv run skill-manager
```

## Configure

Create `./.skill-manager.json` in your project:

```json
{
  "skills": [
    {"name": "read", "repo": "tw93/Waza", "path": "skills/read"},
    {"name": "kami", "repo": "tw93/Kami", "path": "."}
  ]
}
```

- `name`: symlink name under `./.agents/skills/` (single path component; duplicates error)
- `repo`: GitHub `owner/repo` (both parts must be safe slug components)
- `path`: skill directory inside the repo (`.` for repo-root skills; must contain `SKILL.md`; path traversal is rejected)

## Usage

```bash
skill-manager sync                     # clone/fetch sources, link declared skills
skill-manager list                     # show sources and skill status
skill-manager enable                   # interactive TTY: filterable source → multi-select skills
skill-manager enable <repo> <name>...  # non-interactive batch enable (path derived from cache)
skill-manager enable --all ...         # include hidden/internal skills when resolving
skill-manager disable                  # interactive TTY: multi-select enabled skills
skill-manager disable <name>...        # non-interactive batch disable
skill-manager --json sync              # single JSON object on stdout (all commands)
```

Example `list` output — every skill row carries one actionable status word:

```
Sources:
  tw93/Waza  3f2a1c9d  cached
Skills:
  read    tw93/Waza:skills/read  linked
  kami    tw93/Kami:.            unlinked
```

- `linked` = normal; `unlinked` / `broken` = fixable with `skill-manager sync`; `external` = the link points elsewhere and is left alone
- On a TTY, status words and `Error:` / `Warning:` prefixes are colored (`linked` green, `broken` red, `external` / `unlinked` / prefixes yellow); piped output and `--json` stay plain (set `NO_COLOR` to force plain output)
- Paths in `enable` / `disable` / `sync` output are shown relative to the current directory when possible (`.skill-manager.json`, `.agents/skills/read`), else `~`-abbreviated (`~/.skill-manager.json`), else absolute

### Batch enable / disable

`enable` and `disable` accept multiple skills in one invocation:

```bash
skill-manager enable tw93/Waza read write kami   # enable several skills from one repo
skill-manager disable read write                 # disable several skills
```

- **enable is atomic**: every name is validated first; if any is missing or ambiguous, nothing is applied and all problems are reported at once (exit 1). Already-enabled names are idempotent no-ops. The whole batch syncs once.
- **disable is lenient**: disabling a name that is not enabled is a no-op, never an error.
- **Interactive picker** (TTY only): type to filter, ↑/↓ move, space toggle, Enter confirm, Esc clears filter then cancels, Ctrl-C cancels. `enable` is two steps (source, then skills; already-enabled rows are locked). `disable` is one multi-select over current declarations. Non-TTY interactive attempts error with exit 1.

### Global skills

Add `--global` to any of `sync` / `list` / `enable` / `disable` to target user-global skills instead of the project. The user's home is treated as a project: declarations live in `~/.skill-manager.json` and links land in `~/.agents/skills/`. Sources and the cache are shared across scopes.

```bash
skill-manager --global sync                  # link globally-declared skills into ~/.agents/skills/
skill-manager --global list                  # show global skills status
skill-manager --global enable <repo> <name>...  # declare globally + sync (batch)
skill-manager --global disable <name>...        # remove global declarations + links (batch)
skill-manager --json --global list           # JSON output composes with --global
```

Running `skill-manager` from `~` without `--global` targets `~/.skill-manager.json` too (home *is* the global project) — intentional, not a collision.

### Project vs. global scope

The two scopes answer different questions. The project declaration
(`./.skill-manager.json`, committed with the repo) is **team-shared**: it says
which skills the project needs for everyone working on it. The global
declaration (`~/.skill-manager.json`) is **personal preference**: it says which
skills you want everywhere, independent of any project.

Declaring the same skill in both scopes is a supported scenario when both come
from the same source — the project enables it for collaborators, you enable it
globally for yourself. `list` marks such benign overlap with `⊕`; enabling the
same name from *different* sources across scopes is a hard error with a
resolution hint. The source cache is shared: a source is cloned once and both
scopes link against it.

Only `sync` and `source update` ever update already-cached sources. `enable`
and `source add` may clone a missing source (registering it in the global
config) but never pull — a cached clone is used as-is, however stale.

### Source repository management

```bash
skill-manager source list                    # list registered sources and HEAD status
skill-manager source add <owner/repo>        # add and clone a new source
skill-manager source remove <repo>           # remove source (cache + config)
skill-manager source update [repo]           # update one or all sources
skill-manager source available-skills [repo] # list skills in the cache (no project config)
skill-manager source available-skills --all  # include hidden/internal skills
```

`sync` is idempotent and never overwrites an existing non-tool symlink (it skips with a notice). Sources are derived from the declared skills' `repo` fields.

### Cold start (new source, no hand-edited JSON)

```bash
skill-manager source add <owner/repo>              # clone + register globally
skill-manager source available-skills <owner/repo> # optional: discover skill names
skill-manager enable <owner/repo> <name>           # declare in project + sync
```

Non-interactive `enable <owner/repo> <name>` clones a source that is not yet cached and registers it in the global config — the same registration `sync` performs — then declares the skill and syncs the batch. It never pulls: a source that is already cached is used as-is, however stale. The repo-less form `enable <name>` resolves names across cached sources only and never clones; for that form, introduce sources with `source add` (or declare in `.skill-manager.json` and `sync`).

### Source skill discovery

`source available-skills` and `enable` share one scanner over the cached Source checkout:

- **Default**: skip noise directories (`node_modules`, `dist`, `build`, `__pycache__`), directories whose names start with `.` (e.g. `.git`, `.archive`, `.curated`), and skills whose frontmatter has `metadata.internal: true`.
- **`--all`**: include those hidden/internal skills. Skill-root truncation still applies: a directory that contains `SKILL.md` is one skill and is not walked further.
- Declared skill `path` values (including under `.archive/`) are unaffected — `sync` / `list` still honor project config as written.

Layouts like `skills/.curated/...` therefore need `--all` to appear in discovery (stricter default than some installers that whitelist curated paths).

With `--json`, success is `{"ok": true, "data": ...}` and failure is
`{"ok": false, "error": {"code", "message"}}` (exit `0` / `1` / `2` for success /
business error / usage error). Place `--json` before the subcommand:
`skill-manager --json list`, not `skill-manager list --json`.

In **project** scope, `list` and `enable` JSON also include a per-skill boolean
`enabled_globally` (matched by skill **name** against `~/.skill-manager.json`).
It is omitted under `--global`. If the global declaration exists but cannot be
read, the command still succeeds, values are `false`, and a top-level
`warnings: [{code: "global_config_error", message}]` is added. Human `list`
marks the same overlap with `⊕` before the name; interactive enable uses
`✓` (project-locked) and `⊕` (global) glyphs without blocking selection.

## Paths (XDG)

| What | Path |
|------|------|
| Global config | `$XDG_CONFIG_HOME/skill-manager/config.json` (default `~/.config/skill-manager/config.json`) |
| Source cache | `$XDG_CACHE_HOME/skill-manager/repos/` (default `~/.cache/skill-manager/repos/`) |
| Project skills | `./.agents/skills/` |
| Global skills declaration | `~/.skill-manager.json` |
| Global skills | `~/.agents/skills/` |

## Roadmap

The **What** — actionable tickets — lives in the [issue tracker](https://github.com/CNife/skill-manager/issues). This section is the **Why**: the shape skill-manager is growing toward.

### Dual-user design

skill-manager has two users, and every surface must serve both:

- **Humans** — an interactive CLI, and a project config (`.skill-manager.json`) simple enough to read and edit by hand.
- **Agents** — batch-friendly flags and machine-readable output for scripts, CI, and AFK coding agents.

### Any source

A Source shouldn't be locked to `owner/repo` on GitHub. Arbitrary Git URLs and local directories should qualify too. Pinning a Source to a specific commit or tag for reproducibility is a lower-priority future direction.

### One model, project and global

Project Links and user-global Links (`~/.agents/skills/`) share one declaration-and-sync model via the `--global` flag: keep globals slim, declare per project what you actually need.
