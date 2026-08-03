# Skill Manager

按项目声明式启用 coding agent 技能：Skill 只在 Source 中保留一份，项目通过符号链接引用，全局技能保持精简。

## Language

**Skill / 技能**:
含 `SKILL.md` 的目录，可被遵守 `./.agents/skills/`（及可选 `~/.agents/skills/`）约定的 coding agent 启用。
_Avoid_: package, installable, 包

**Source / 源仓库**:
以 `owner/repo` 标识的 GitHub 仓库，克隆进缓存后可提供一个或多个 Skill。
_Avoid_: registry, remote, 远端, 注册表

**Skill declaration / 技能声明**:
项目或全局技能声明文件中的一条启用记录，由 `name`、`repo`、`path` 组成。
_Avoid_: skill ref, entry, 安装项

**Project config / 项目配置**:
`./.skill-manager.json`，声明本项目启用哪些 Skill。
_Avoid_: manifest, lockfile, 清单

**Global config / 全局配置**:
XDG 配置文件（默认 `~/.config/skill-manager/config.json`），记录各 Source 及其 HEAD commit。
_Avoid_: ledger, database, 台账

**Global skills declaration / 全局技能声明**:
`~/.skill-manager.json`，声明用户全局启用哪些 Skill；与项目配置同形（`{skills:[...]}`）。与 Global config 区分：本文件只存技能声明。
_Avoid_: global config, user manifest

**Scope / 作用域**:
命令作用的目标范围：`project`（默认，`./`）或 `global`（`--global`，用户 home）。两者共享 Source 与缓存，仅声明文件与 Link 目录不同。
_Avoid_: mode, level

**Source cache / 源缓存**:
XDG 缓存目录（默认 `~/.cache/skill-manager/repos/`），存放已克隆的 Source。
_Avoid_: library, global library, 全局技能库

**Link / 链接**:
`./.agents/skills/<name>`（项目）或 `~/.agents/skills/<name>`（全局）下的符号链接，指向源缓存内某个 Skill 目录。
_Avoid_: install, copy, 安装

**sync / 同步**:
幂等操作：确保 Source 已克隆、记录 HEAD、为已声明 Skill 创建 Link。作用于当前 Scope（项目或全局）。
_Avoid_: restore, apply, 恢复, 应用

**enable / 启用**:
向当前 Scope 的技能声明文件追加一条或多条 Skill declaration 并执行 sync；TTY 下可交互筛选勾选（先 Source 再 Skill），也可 `enable <repo> <name>…` 非交互批量启用（整批原子：任一名字无效则整批不生效）。
_Avoid_: add, install, 添加, 安装

**disable / 禁用**:
从当前 Scope 的技能声明文件移除一条或多条 Skill declaration 并清理对应 Link；TTY 下可交互多选已启用项，也可 `disable <name>…` 非交互批量禁用（宽松：未启用的名字为幂等 no-op）。
_Avoid_: remove, uninstall, 删除, 卸载

## Dual-scope model

Two declaration scopes share one model, one Source cache, and one Global config
(Sources are global by design; only declarations and Links are per-scope):

- **Project scope** (default): `./.skill-manager.json` + `./.agents/skills/`. The
  declaration is **team-shared** — it is committed with the repo and answers
  "which Skills does this project need for everyone working on it?".
- **Global scope** (`--global`, the user's home): `~/.skill-manager.json` +
  `~/.agents/skills/`. The declaration is **personal preference** — it answers
  "which Skills do I want everywhere, independent of any project?".

Same-name overlap across scopes is a **supported scenario** when both scopes
reference the same Source (same repo and path): the project declares a Skill for
its collaborators while the user declares the same Skill globally for
themselves. `list` marks this benign overlap with `⊕`; interactive `enable`
keeps the neutral hint "also enabled globally (same source) — no conflict".
Overlap from *different* Sources is a conflict: `enable` hard-errors with a
resolution hint in both directions (project and `--global`).

**Cache invariant**: only `sync` and `source update` may *update* already
cached content. `enable <repo> <name>` and `source add` may clone a missing
Source into the cache and register it in the Global config, but they never pull
— an existing cached clone is left untouched.
