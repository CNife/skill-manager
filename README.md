# Skill Manager

Project-scoped declarative skill manager for coding agents that load skills from `./.agents/skills/` (and optionally `~/.agents/skills/`).

**One source, many projects, slim globals.** Each skill lives once in a cached Source. Projects get symlinks (Links), not copies. Keep agent-global skills minimal; declare per project what you actually need.

Compatible with any agent that discovers skill directories containing `SKILL.md` under those paths ([pi](https://github.com/earendil-works/pi-coding-agent) is one example).

Today skill-manager only writes project Links under `./.agents/skills/`. Managing `~/.agents/skills/` is on the [roadmap](#roadmap).

## Features

- **Declare and sync project skills** — write `.skill-manager.json`, run `skill-manager sync`
- **Inspect source and link status** — `skill-manager list` (`linked` / `external` / `broken` / `unlinked`)
- **Enable / disable interactively** — `skill-manager enable` / `disable`
- **Manage source cache** — `skill-manager source list|add|remove|update`

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
skill-manager sync      # clone/fetch sources, link declared skills
skill-manager list      # show sources and skill status
skill-manager enable    # interactively add a skill from the cache, then sync
skill-manager disable   # interactively remove a skill and its link
```

### Source repository management

```bash
skill-manager source list              # list cached repos and HEAD status
skill-manager source add <owner/repo>  # add and clone a new source
skill-manager source remove <repo>     # remove source (cache + config)
skill-manager source update [repo]     # update one or all sources
```

`sync` is idempotent and never overwrites an existing non-tool symlink (it skips with a notice). Sources are derived from the declared skills' `repo` fields.

## Paths (XDG)

| What | Path |
|------|------|
| Global config | `$XDG_CONFIG_HOME/skill-manager/config.json` (default `~/.config/skill-manager/config.json`) |
| Source cache | `$XDG_CACHE_HOME/skill-manager/repos/` (default `~/.cache/skill-manager/repos/`) |
| Project skills | `./.agents/skills/` |

## Roadmap

The **What** — actionable tickets — lives in the [issue tracker](https://github.com/CNife/skill-manager/issues). This section is the **Why**: the shape skill-manager is growing toward.

### Dual-user design

skill-manager has two users, and every surface must serve both:

- **Humans** — an interactive CLI, and a project config (`.skill-manager.json`) simple enough to read and edit by hand.
- **Agents** — batch-friendly flags and machine-readable output for scripts, CI, and AFK coding agents.

### Any source

A Source shouldn't be locked to `owner/repo` on GitHub. Arbitrary Git URLs and local directories should qualify too. Pinning a Source to a specific commit or tag for reproducibility is a lower-priority future direction.

### One model, project and global

Project Links and user-global Links (`~/.agents/skills/`) should share one declaration-and-sync model: keep globals slim, declare per project what you actually need.
