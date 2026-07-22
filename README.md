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

- [ ] **Global skills** — manage Links under `~/.agents/skills/` (same model as project Links)
- [ ] **Non-interactive enable/disable** — CLI flags for scripts and CI
- [ ] **Broader sources** — arbitrary Git URLs; local git repos or folders as Sources
- [ ] **Source inspect** — list skill paths in a Source before enabling
- [ ] **Hidden-dir scan policy** — skip skills under hidden directories by default; `--all` to include them
- [ ] **Pinned versions** — pin a Source to a commit or tag in the project declaration
