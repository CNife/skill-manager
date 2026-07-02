# Skill Manager

Project-scoped declarative skill manager for [pi](https://github.com/earendil-works/pi-coding-agent) agent skills.

Keep global skills minimal; declare per-project which skills to enable. `skill-manager` clones GitHub skill source repos into an XDG cache and symlinks selected skills into your project's `./.agents/skills/`.

## Install

```bash
uv tool install .  # or run in-place: uv run skill-manager
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

- `name`: symlink name under `./.agents/skills/` (must be a single path component; duplicates error)
- `repo`: GitHub `owner/repo` (both parts must be safe slug components)
- `path`: skill directory inside the repo (`.` for repo-root skills; must contain `SKILL.md`; path traversal is rejected)

## Usage

```bash
skill-manager sync   # clone/fetch sources, link declared skills
skill-manager list   # show sources and skill status
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
