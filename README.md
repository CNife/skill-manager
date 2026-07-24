# Skill Manager

Project-scoped declarative skill manager for coding agents that load skills from `./.agents/skills/` (and optionally `~/.agents/skills/`).

**One source, many projects, slim globals.** Each skill lives once in a cached Source. Projects get symlinks (Links), not copies. Keep agent-global skills minimal; declare per project what you actually need.

Compatible with any agent that discovers skill directories containing `SKILL.md` under those paths ([pi](https://github.com/earendil-works/pi-coding-agent) is one example).

Project Links live under `./.agents/skills/`; user-global Links under `~/.agents/skills/` are managed with the same model via `--global` (see [Global skills](#global-skills)).

## Features

- **Declare and sync project skills** — write `.skill-manager.json`, run `skill-manager sync`
- **Inspect source and link status** — `skill-manager list` (`linked` / `external` / `broken` / `unlinked`)
- **Enable / disable** — interactive menus, or non-interactive `enable <repo> <name>` / `disable <name>`
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
skill-manager enable                   # interactive: pick cached repo + skill, then sync
skill-manager enable <repo> <name>     # non-interactive enable (path derived from cache)
skill-manager enable --all ...         # include hidden/internal skills when resolving
skill-manager disable                  # interactive: pick an enabled skill
skill-manager disable <name>           # non-interactive disable
skill-manager --json sync              # single JSON object on stdout (all commands)
```

### Global skills

Add `--global` to any of `sync` / `list` / `enable` / `disable` to target user-global skills instead of the project. The user's home is treated as a project: declarations live in `~/.skill-manager.json` and links land in `~/.agents/skills/`. Sources and the cache are shared across scopes.

```bash
skill-manager --global sync                  # link globally-declared skills into ~/.agents/skills/
skill-manager --global list                  # show global skills status
skill-manager --global enable <repo> <name>  # declare globally + sync
skill-manager --global disable <name>        # remove a global declaration + link
skill-manager --json --global list           # JSON output composes with --global
```

Running `skill-manager` from `~` without `--global` targets `~/.skill-manager.json` too (home *is* the global project) — intentional, not a collision.

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

`enable` does not clone; introduce sources with `source add` (or declare in `.skill-manager.json` and `sync`).

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
