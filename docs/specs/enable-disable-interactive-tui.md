# Spec: enable / disable interactive TUI

**Status**: ready for implementation  
**Map**: [enable/disable 交互 TUI 规格](https://github.com/CNife/skill-manager/issues/30)  
**Compiled from**:

- [Research: Python TUI 库能否支撑 enable/disable picker](https://github.com/CNife/skill-manager/issues/32)
- [Prototype: enable/disable picker 外观与键位](https://github.com/CNife/skill-manager/issues/31)
- [Grilling: SKILL.md 详情区抽取规则](https://github.com/CNife/skill-manager/issues/33)
- [Grilling: picker 边界与提交规则](https://github.com/CNife/skill-manager/issues/34)
- [汇成 enable/disable 交互可开工规格](https://github.com/CNife/skill-manager/issues/35)

**Prototype reference** (throwaway, appearance/keys only): branch `prototype/picker-appearance`, `docs/prototype/picker_appearance.py --variant A`.

Domain terms follow `CONTEXT.md`. Collaboration language is Chinese; **user-facing CLI strings are English** (`AGENTS.md`).

---

## 1. Scope

### In scope

Replace the current numbered `input()` menus for **interactive** `enable` and `disable` with a type-to-filter checkbox/select TUI:

| Command | Mode | Flow |
|---|---|---|
| `skill-manager enable` (no `REPO` / `NAME`) | interactive | **Two steps**: Source filterable single-select → Skills in that Source filterable multi-select |
| `skill-manager disable` (no `NAME`) | interactive | **One step**: currently-enabled skills in Scope, filterable multi-select |

Also in scope as **preconditions of the interactive surface** (apply wherever Source scan feeds enable / available-skills):

- Skill discovery gate (UTF-8 + frontmatter string `name` / `description`)
- Discovery name = frontmatter `name` (not directory basename; drop repo-root lowercase special case)

### Out of scope (do not change in this work)

- Non-interactive batch semantics of `enable <repo> <name>…` / `disable <name>…` (atomic enable, lenient disable, JSON `results` shape, exit codes for those paths) — keep as today ([ADR 0011](../adr/0011-batch-enable-disable.md)).
- Production test plan details beyond “existing tests keep passing; add coverage for new scan gate + TUI entry guards”.
- Replicating vercel-labs/skills agent install wizard, universal locked agents, install modes.
- Extracting the picker into a public/reusable module for other commands (internal helper shared by enable/disable is fine; no extra API surface).
- Fine-grained color themes / full `NO_COLOR` policy beyond the minimum in §8.
- Changing `sync` / `list` / source management commands.

---

## 2. Dependency and platform

### Library

| Item | Decision |
|---|---|
| Primary dependency | **`questionary`** (need **≥ 2.1.1** capabilities: `use_search_filter`, `Choice(disabled=…)`, `Choice.description`) |
| Underlying stack | `prompt_toolkit` (+ its deps, e.g. `wcwidth`) — transitive via questionary |
| Thin customization | **Allowed** on questionary / prompt_toolkit for the two UX tweaks proven in the prototype: (1) Esc clears filter then cancels; (2) dim description to the right of the name on the highlighted row only |
| Forbidden extras | Do **not** add InquirerPy, simple-term-menu, Rich, or Textual for this feature |
| Count | Exactly **one** dedicated TUI library (questionary); no second picker stack |

### Platform support

| Environment | Expectation |
|---|---|
| Unix TTY (Linux, macOS) | Fully supported |
| Windows Terminal / Git Bash TTY | Supported (prompt_toolkit path) |
| Legacy Windows `cmd.exe` | Best-effort / degraded; not a release blocker |
| Non-TTY stdin or stdout | Interactive path **must not** start; fail per §6 |

---

## 3. When the interactive path runs

Interactive picker starts **only** when all of the following hold:

1. Command is `enable` with neither `REPO` nor any `NAME`, **or** `disable` with no `NAME`s.
2. Root `--json` is **not** set (`--json` implies non-interactive; already true today).
3. **Both** stdin and stdout are a TTY (`sys.stdin.isatty()` and `sys.stdout.isatty()`).

Otherwise:

| Situation | Behavior |
|---|---|
| `--json` and missing required non-interactive args | Usage error, English message (existing), **exit 2**, JSON error envelope when `--json` |
| No `--json`, missing args, but **not** a TTY | Do **not** fall back to numbered menus. English error on stderr, **exit 1**, code `not_found` is wrong — use a clear message such as `Error: interactive enable requires a TTY; pass REPO and NAME(s), or run in a terminal.` (and the disable twin). Prefer mapping via existing error helpers; a dedicated message string is required, exit **≠ 0** (use **1** unless classified as usage → 2). **Normative**: treat as business failure **exit 1** with `Error: …` on stderr (non-JSON). |
| `REPO` without `NAME` on enable | Unchanged usage error (exit 2) |

Non-interactive success/failure paths are unchanged.

---

## 4. Skill discovery gate (scan)

All Source scans that feed **interactive enable**, **non-interactive enable resolution**, and **`source available-skills`** share one gate. Default noise filters (`node_modules` / `dist` / `build` / `__pycache__` / dot-dirs / `metadata.internal: true`) and skill-root truncation remain as [ADR 0009](../adr/0009-source-scan-filtering.md). `--all` still only widens those noise/internal filters; it does **not** relax the gate below.

A directory is a **qualified skill** iff all hold:

1. It contains a file literally named `SKILL.md`.
2. `SKILL.md` decodes as **UTF-8**; otherwise **reject** (not listed, not enableable).
3. YAML frontmatter is present and contains **non-empty string** fields `name` and `description`. Missing, non-string, or empty → **reject**.
4. **Discovery name** = frontmatter `name`. **Do not** use directory basename. **Remove** the special case “repo-root skill → lowercase repo name”.
5. **`path`** remains the skill directory relative to the Source cache root; repo-root skill keeps `path: "."`.
6. Unqualified trees are treated as **absent** for listing and enable resolution.

Identity of a declaration remains `name + repo + path` ([ADR 0003](../adr/0003-explicit-skill-name.md)); only the **default `name` produced by scan** changes source (FM `name` instead of folder name).

`sync` / `list` continue to honor **already-declared** `path`s without re-applying this gate to drop them from config. Links still require `SKILL.md` at apply time (existing `LinkError` behavior).

---

## 5. Detail / description rules

### 5.1 Skill row (enable · multi-select step)

| Rule | Value |
|---|---|
| Source text | Frontmatter string `description` only — no body fallback, no placeholder like `(no description)` |
| Placement | Single line, **dim**, to the **right of the name**, **highlighted row only** |
| Preprocess (before measuring width) | Strip ANSI/control chars; collapse `\r`/`\n`/runs of whitespace to one space; trim |
| Truncation | By **remaining terminal display columns** on that row (East Asian fullwidth = 2 columns, not `len()`); cut by character, **not** word boundary; append `…` when truncated |
| `avail < 2` | Render **no** description (avoid a lone `…`) |

Unqualified skills never appear, so missing-description placeholders are unnecessary.

### 5.2 Source row (enable · single-select step)

| Rule | Value |
|---|---|
| Label | `owner/repo` |
| Detail | Dim single line to the right of the label on the highlighted row: count of **qualified** skills under that cache, English, e.g. `3 skills` / `1 skill` / `0 skills` (implementer picks singular/plural consistently) |

### 5.3 disable list

- **No** SKILL.md description detail.
- Row label = declaration `name` (primary). Optional secondary hints (`repo` / path) are **not required** by this spec; if added for disambiguation they must not participate in filter matching (§7).

---

## 6. Command flows and empty-set guards

### 6.1 `enable` (interactive)

```
guard TTY / not --json
list cached Sources
  empty → error, no picker, exit 1
Source single-select (filterable)
  user cancels → no writes, exit 1
  selected Source has 0 qualified skills → error, no Skill step, exit 1
Skill multi-select (filterable; locked rows for already-enabled names)
  user cancels → no writes, exit 1
  submit with zero new (non-locked) selections → message, exit 0, no write, no sync
  submit with ≥1 new selection → apply batch (existing _enable_apply_batch semantics), then sync if anything added
```

**Normative English messages** (exact or equivalent; keep stable enough for tests):

| Case | stderr/stdout | exit |
|---|---|---|
| No cached Source | e.g. `No cached repos found. Run 'skill-manager sync' first to populate the cache.` (existing `NotFoundError` text OK) | 1 |
| Selected Source has 0 qualified skills | e.g. `No skills found in {repo}` (update copy if scan gate changes “what counts”; still `NotFoundError`) | 1 |
| Cancel (Esc on empty filter / Ctrl-C) | e.g. `Cancelled — no changes.` (or silent cancel); **no** config write | 1 |
| Empty submit (no new skills) | e.g. `Nothing to enable.` | 0 |
| Non-TTY interactive attempt | e.g. `interactive enable requires a TTY; pass REPO and NAME(s), or run in a terminal.` | 1 |

**Locked rows (already enabled in current Scope):**

- Any skill whose discovery name is already present in the current Scope declarations appears as **locked**.
- Display: `- {name} (already enabled)`, dim/italic (questionary disabled styling).
- Highlightable so description still shows; `space` is a **no-op**.
- Locked rows are **not** part of the submit set.
- Skills that are enabled in the declaration but **no longer qualified** (or missing) in cache: **do not** appear on the enable picker (they are not in the qualified scan set). They remain on the disable picker via the declaration list.

**0-skill Sources:** still listed in the Source step with detail `0 skills`; choosing one fails immediately (no empty Skill multi-select).

### 6.2 `disable` (interactive)

```
guard TTY / not --json
load current Scope declarations
  no enabled skills → message, no picker, exit 0
multi-select over declaration entries (no description detail)
  cancel → no writes, exit 1
  empty submit → message, exit 0, no write, no link cleanup
  ≥1 selected → existing batch disable apply
```

| Case | message | exit |
|---|---|---|
| No enabled skills | `No enabled skills to disable.` (existing emit OK) | **0** |
| Cancel | same as enable cancel | 1 |
| Empty submit | e.g. `Nothing to disable.` | 0 |
| Non-TTY | disable twin of enable non-TTY message | 1 |

Empty submit is **not** cancel. Cancel is only Esc (empty filter) / Ctrl-C.

Interactive empty submit does **not** need per-name `already_enabled` / `not_enabled` chatter; that remains a non-interactive concern.

### 6.3 Apply / sync relationship

Unchanged:

- enable: append new declarations, single `sync` only if at least one new skill was added; already-enabled names are idempotent when they appear in a resolved batch.
- disable: remove selected declarations, clean links per existing rules; lenient for names not enabled in non-interactive mode.

Interactive enable submit set = checked **and not locked** items only.

### 6.4 Scope (`project` / `global`)

Unchanged:

- `--global` selects global declaration file + `~/.agents/skills/`.
- Source cache shared across scopes.
- Project declaration missing stays strict; global enable may create missing `~/.skill-manager.json` ([ADR 0010](../adr/0010-global-skills-home-as-project.md)).

---

## 7. Appearance and keybindings

### Look

- Minimal list. **No** clack-style vertical rail (`│` / `◆` / `└` / similar).
- Pointer: `❯`
- Multi-select indicators: `●` selected / `○` unselected
- Locked: `- name (already enabled)` (see §6.1)
- Description: §5
- Question mark / instruction line: questionary defaults acceptable; keep copy English

### Keys

| Action | Binding |
|---|---|
| Filter | Type printable characters (type-to-filter) |
| Clear filter | `Esc` when filter is non-empty |
| Cancel | `Esc` when filter is already empty, **or** `Ctrl-C` anytime → no changes, exit 1 |
| Move | `↑` / `↓` and `Ctrl-P` / `Ctrl-N` |
| Toggle check | `space` (no-op on locked) |
| Confirm | `Enter` |
| PageUp / PageDown | **Not** required |

### Filter behavior

| Rule | Value |
|---|---|
| Match algorithm | Case-insensitive **substring** (`contains`); not fuzzy, not tokenized, not regex |
| Match field | **Primary label only**: Source → `owner/repo`; enable Skill → FM `name`; disable → declaration `name` |
| Non-fields | description, skill counts, path, repo annotations **do not** match |
| No matches | Follow questionary default: “no match” styling on the filter string (e.g. red) + **list falls back to full unfiltered set** (no empty list, no mandatory “No matches” row) |
| Checks vs visibility | Filtering only changes visibility; checked state is preserved across filter edits |
| Submit set | All checked non-locked items, whether or not visible under the current filter |

### Long lists

Visible row count follows **current terminal height** (library/viewport). Spec does **not** hard-code a row limit. Overflow scrolls with ↑↓ (and Ctrl-N/P).

### Same-name skills (different paths)

- **No special product UX** (no dedicated disambiguation chrome, no mutual exclusion).
- Implementation backstop (not a feature): if the submit set contains multiple entries with the same name, **dedupe by name keeping the first in list order** (same idea as today’s interactive enable).
- Non-interactive `enable <repo> <name>` **ambiguity → atomic failure** stays as today; this spec does not redesign that path.

---

## 8. Color / width / terminal minimums

Normative minimum only:

1. Dim / disabled / description styles must remain readable on a common dark terminal; exact palette may follow the prototype Style.
2. Width math for description truncation must use display columns (fullwidth-aware), not raw Python string length.
3. If `NO_COLOR` is set in the environment, prefer disabling chromatic styling when cheap via existing library hooks; **do not** block the feature on a perfect NO_COLOR implementation.
4. Redraw / resize glitches: best-effort; no custom resize protocol required beyond prompt_toolkit defaults.

---

## 9. Exit code summary

Aligned with existing CLI convention (`0` success, `1` business failure, `2` usage):

| Situation | exit |
|---|---|
| enable/disable applied successfully (incl. idempotent already-enabled in non-interactive) | 0 |
| disable: no enabled skills (interactive empty set) | 0 |
| enable/disable: empty submit (nothing new to do) | 0 |
| Cancel (Ctrl-C / Esc on empty filter) | 1 |
| No cached Source; 0-skill Source selected; other `NotFoundError` | 1 |
| Non-TTY interactive attempt | 1 |
| `--json` without required non-interactive args; enable `REPO` without `NAME` | 2 |
| Config / source / link failures | 1 (existing mapping) |

Cancel must never write declarations or run sync.

---

## 10. Relationship to non-interactive API

| Surface | Change? |
|---|---|
| CLI shape `enable [REPO NAMES…]`, `disable [NAMES…]`, `--all`, `--global`, `--json` | No |
| JSON success envelope `{"ok": true, "data": {"results": […]}}` (+ `sync` on enable) | No |
| enable batch atomic validation + multi-error message | No |
| disable lenient `not_enabled` | No |
| Scan-backed name resolution | **Yes** — discovery name and qualification gate per §4 (affects which names resolve) |
| Interactive menus | **Yes** — replace `_numbered_select` / `_numbered_multi_select` usage on these two commands |

After this work, numbered menus should no longer be the interactive path for enable/disable. Helpers may be deleted if unused.

---

## 11. Implementation notes (non-normative)

- Prototype on `prototype/picker-appearance` is the visual/key reference for Variant A; port the Esc-filter and inline-description control ideas, do not import throwaway code into the package as-is.
- Prefer a small internal module (e.g. picker helpers used only by enable/disable) over bloating `cli.py`, but module layout is an implementer choice.
- Keep typer as the CLI framework; questionary runs only inside the interactive branch.
- Tests: unit-test scan gate and description truncation without a TTY; guard non-TTY / `--json` entry; avoid brittle full-screen TUI tests unless the stack makes them cheap.
- Research note on `research/tui-picker-libs` ranked InquirerPy first because it assumed questionary had no filter — **superseded** by prototype findings on questionary 2.1.1. Do not reintroduce InquirerPy from that ranking.

---

## 12. Acceptance checklist

Implementer is done when:

- [ ] `questionary` is the sole added TUI dependency; no clack rail in the UI
- [ ] Interactive enable is Source select → Skill multi-select with type-to-filter, locked already-enabled rows, inline dim descriptions
- [ ] Interactive disable is one multi-select over Scope declarations, no description detail
- [ ] Esc clears filter then cancels; Ctrl-C cancels; empty submit exits 0; cancel exits 1; no writes on cancel/empty
- [ ] Non-TTY and `--json` never open the picker
- [ ] Scan gate + FM `name` discovery applied consistently to enable resolution and available-skills
- [ ] 0-skill Source listed but rejected on select; no-cached-Source and disable-empty-set behaviors match §6
- [ ] Non-interactive batch enable/disable behavior and JSON shapes unchanged except via the shared scan gate
- [ ] User-visible new/changed strings are English

---

## 13. Decision index

| Topic | Source ticket |
|---|---|
| Library shortlist → questionary primary | #32 research, corrected by #31 |
| Look, keys, Esc, locked chrome, inline detail | #31 prototype |
| FM description only; scan gate; Source count detail; disable no detail | #33 grilling |
| Empty sets, empty submit vs cancel, filter rules, long list, same-name | #34 grilling |
| This document | #35 task |
