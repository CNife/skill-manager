# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/adr/`** — ADRs that touch the area you're about to work in

If any of these are missing, **proceed silently**. `/domain-modeling` (via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions resolve.

## File structure

Single-context:

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-symlink-not-copy.md
│   ├── 0002-sources-derived-from-declarations.md
│   ├── 0003-explicit-skill-name.md
│   └── 0004-sync-add-only.md
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (issue title, refactor proposal, hypothesis, test name), use the term as defined in `CONTEXT.md`. Synonyms under `_Avoid_` are out of bounds.

If the concept isn't in the glossary yet — either inventing language the project doesn't use (reconsider), or a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it rather than silently overriding:

> _Contradicts ADR-0004 (sync add-only) — but worth reopening because…_
