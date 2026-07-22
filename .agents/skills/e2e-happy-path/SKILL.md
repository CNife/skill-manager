---
name: e2e-happy-path
description: End-to-end happy-path test for skill-manager — declares the public fixture, syncs, and asserts the correct path. Run before PR.
disable-model-invocation: true
---

# E2E happy-path test for skill-manager

Verify skill-manager's **correct path** end to end: a declared skill is cloned from a real public GitHub repo, linked into `.agents/skills/`, and its `SKILL.md` stays readable. The test object is skill-manager itself.

The split is fixed: a bash **script** runs the deterministic flow (isolate, declare, sync) and emits a **manifest**; you, the agent, do the orchestration, assertions, and human-readable verdict.

## Preconditions

- Run from the **skill-manager repo root** (cwd = repo root). The script path below is relative to it.
- `skill-manager` is installed and on PATH.
- GitHub is reachable for anonymous HTTPS clone (the fixture repo is public).

## Steps

### 1. Run setup + sync

Execute the script and capture the full stdout and exit code. It builds an isolated tree, redirects XDG, writes the fixture declaration, and runs `skill-manager sync`. It prints `skill-manager sync` output followed by exactly one `E2E_MANIFEST {...}` line on exit.

```bash
.agents/skills/e2e-happy-path/scripts/e2e-setup-sync.sh
```

**Completion criterion**: you hold the script's exit code and full stdout, and the `E2E_MANIFEST {...}` line is present in it.

### 2. Route on exit code

- **Non-zero exit**: an **infra failure** — setup or sync broke. Grep the `E2E_MANIFEST` line for `root` (preserve that tmpdir for triage), read stderr, and go straight to the verdict.
- **Zero exit**: sync succeeded; continue to assertion.

**Completion criterion**: you have routed to exactly one path — infra failure (verdict) or sync success (assertion).

### 3. Parse the manifest

From the `E2E_MANIFEST {...}` line, parse the JSON and take four paths: `root`, `project_dir`, `xdg_config_home`, `xdg_cache_home`.

```bash
grep '^E2E_MANIFEST ' <<<"$STDOUT" | sed 's/^E2E_MANIFEST //'
```

**Completion criterion**: all four paths are non-empty.

### 4. Run the thorough gate

Assert all five points below. Each records pass/fail with evidence. **Run every point to the end even if an earlier one fails** - the gate is exhaustive so nothing is under-reported; one fail makes the whole run fail, but the rest still execute.

Let `REPO=CNife/skill-manager-e2e-fixture` and `CACHE=$xdg_cache_home/skill-manager/repos/$REPO`.

1. **Symlink valid**: `$project_dir/.agents/skills/e2e-fixture` is a symlink whose resolved target equals `$CACHE`.
2. **SKILL.md readable**: reading `SKILL.md` through that symlink, its first line is `---`.
3. **Ledger HEAD consistent**: in `$xdg_config_home/skill-manager/config.json`, `sources["CNife/skill-manager-e2e-fixture"].commit` equals `git -C "$CACHE" rev-parse HEAD` (compare the two live values).
4. **Clone happened**: `$CACHE/.git` exists.
5. **sync output**: the script stdout excluding the `E2E_MANIFEST` line contains both `ensured CNife/skill-manager-e2e-fixture` and `created e2e-fixture`.

**Completion criterion**: every one of the five points has been asserted and carries a pass/fail plus evidence.

### 5. Verdict

- **All five pass**: verdict is **PASS**; you may delete `$root`.
- **Any fail**: verdict is **FAIL**; list each failing point with its evidence, and **preserve `$root`** for triage, stating the path in the verdict.
- **Infra failure** (step 2): verdict is **FAIL (infra)**; summarize stderr and state `$root`, preserving it.

**Completion criterion**: a single unambiguous PASS or FAIL verdict has been emitted, and on any FAIL the `$root` path is stated and the tmpdir preserved.
