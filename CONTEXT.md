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
项目配置中的一条启用记录，由 `name`、`repo`、`path` 组成。
_Avoid_: skill ref, entry, 安装项

**Project config / 项目配置**:
`./.skill-manager.json`，声明本项目启用哪些 Skill。
_Avoid_: manifest, lockfile, 清单

**Global config / 全局配置**:
XDG 配置文件（默认 `~/.config/skill-manager/config.json`），记录各 Source 及其 HEAD commit。
_Avoid_: ledger, database, 台账

**Source cache / 源缓存**:
XDG 缓存目录（默认 `~/.cache/skill-manager/repos/`），存放已克隆的 Source。
_Avoid_: library, global library, 全局技能库

**Link / 链接**:
`./.agents/skills/<name>` 下的符号链接，指向源缓存内某个 Skill 目录。
_Avoid_: install, copy, 安装

**sync / 同步**:
幂等操作：确保 Source 已克隆、记录 HEAD、为已声明 Skill 创建 Link。
_Avoid_: restore, apply, 恢复, 应用

**enable / 启用**:
向项目配置追加一条 Skill declaration 并执行 sync；可交互选单，也可 `enable <repo> <name>` 非交互。
_Avoid_: add, install, 添加, 安装

**disable / 禁用**:
从项目配置移除一条 Skill declaration 并清理对应 Link；可交互选单，也可 `disable <name>` 非交互。
_Avoid_: remove, uninstall, 删除, 卸载
