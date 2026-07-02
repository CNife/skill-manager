---
date: 2026-07-02T09:31:42+0800
author: 蔡涛
commit: 40d64ed
branch: main
repository: skill-manager
topic: "interactive enable/disable commands"
tags: [intent, frd, skill-manager, cli, enable, disable]
status: ready
last_updated: 2026-07-02T09:31:42+0800
last_updated_by: 蔡涛
---

# FRD: Interactive Enable/Disable Commands

## Summary

Add `skill-manager enable` and `skill-manager disable` interactive commands that allow users to add/remove skills from the project without manually editing `.skill-manager.json`. `enable` lists cached repositories, scans for available skills (directories containing `SKILL.md`), lets the user pick, then appends to config and runs sync. `disable` lists currently enabled skills, lets the user pick one, then removes it from config and cleans up the symlink.

## Problem & Intent

用户在 FRD 中的原始意图明确了第一版 MVP（sync + list）不做 enable/disable 交互命令，但这是项目启用层的自然演进。现在 MVP 已交付并验证通过，用户希望补上 enable/disable 两个命令：

> 现在需要 add / remove 功能，从当前项目中添加和删除技能

后续澄清：命令采用交互式模式，从缓存仓库的可用技能列表中选择，替代手动编辑 JSON 文件。命令名称为 `enable`/`disable`（与项目已有术语一致，`skill-manager list/enable/disable/project list/sync`）。

## Goals

- 允许用户通过交互式命令启用技能，无需手动编辑 `.skill-manager.json`
- 允许用户通过交互式命令禁用技能，同时清理对应 symlink
- enable 自动扫描缓存仓库中的 `SKILL.md` 目录，提供准确的技能候选列表
- enable/disable 修改配置后自动执行 sync，一步到位
- 与现有声明式模型一致：最终产物仍是 `.skill-manager.json` 的修改 + sync 同步

## Non-Goals

- 不做非 GitHub 远端（第一版只认 `owner/repo`）
- 不做 GitHub API 搜索（技能来源限定在已缓存的仓库）
- 不做 TUI / curses 交互式勾选 UI
- 不实现 Pi 扩展（不监听目录切换事件）
- 不引入数据库、HTTP 服务器、GUI
- 不维护全局「已启用技能」注册表
- 不做 `enable <name>` 单参数快捷模式（本次仅实现交互式）

## Functional Requirements

1. `skill-manager enable`（无参数）进入交互模式：
   - 扫描 `~/.cache/skill-manager/repos/` 列出所有已缓存的仓库
   - 用户选择仓库 → 扫描该仓库中所有含 `SKILL.md` 的目录作为候选技能
   - 用户选择技能 → `name` 自动取自目录名（根目录 `.` 技能取 repo 名转小写）
   - 检测同名技能是否已存在 → 是则提示跳过，不修改
   - 追加 `{name, repo, path}` 到 `.skill-manager.json` 的 `skills` 数组
   - 自动执行 sync：clone（如需要）/pull 仓库 → 创建 symlink
   - 输出操作结果
2. 无缓存仓库时，提示用户先执行 `sync` 已有配置或手动添加，然后重试
3. `skill-manager disable`（无参数）进入交互模式：
   - 读取当前项目 `.skill-manager.json`，列出已启用的技能
   - 用户选择技能 → 从 `skills` 数组中删除条目
   - 删除 `./.agents/skills/<name>` 下的 symlink（如果存在且是本工具创建的）
   - 输出操作结果
4. 配置修改操作幂等：重复 `disable` 已禁用的技能 → 提示不存在；重复 `enable` 已启用的技能 → 提示已存在
5. 支持 `save_project_config()` 函数——当前项目中缺失，需新增

## Non-Functional Requirements

- **Performance**: 无特殊要求。sync 受 Git clone/fetch 速度支配，属可接受范围
- **Security**: 只操作 XDG 缓存和项目 `.agents/skills/` 目录；不操作不受信任的用户输入以外的系统区域
- **UX / Accessibility**: 交互式选择列表清晰，有编号和状态标注；错误信息包含具体路径和原因
- **Reliability**: 配置修改在写入前校验完整性（JSON 格式、重名等）；写入失败时保持原文件不变

## Constraints & Assumptions

- 实现语言：Python + typer，复用现有 `src/skill_manager/` 包结构
- 需要新增 `config.save_project_config()` 函数（当前只有 `load_project_config`）
- 交互选择用 typer 内置能力或简单的 `input()`/`select()` 实现，不引入第三方交互库
- 缓存仓库路径：`~/.cache/skill-manager/repos/<owner>/<repo>`
- 项目配置位置：`./.skill-manager.json`

## Acceptance Criteria

- [ ] 在有一个或多个缓存仓库的项目中运行 `skill-manager enable`，进入仓库选择 → 技能选择 → 追加配置 → sync 完成，退出 0
- [ ] `enable` 后 `.skill-manager.json` 中新增了正确的 `{name, repo, path}` 条目
- [ ] 无缓存仓库时 `enable` 打印提示信息并退出非 0
- [ ] `enable` 一个已启用的技能时，提示已存在并退出非 0
- [ ] 运行 `skill-manager disable` 进入交互选择，选择后配置条目和 symlink 均已删除
- [ ] 所有已启用的技能都删除后再次 `disable`，提示无技能可禁用
- [ ] `uv run pytest` 全部通过（63 + 新增用例）
- [ ] `ruff check .` 和 `ruff format --check .` 通过

## Recommended Approach

在 cli.py 中新增 `@app.command()` 注册 `enable` 和 `disable` 命令，新增 `run_enable`/`run_disable` 编排函数。config.py 中新增 `save_project_config()` 用于写回项目配置。enable 的仓库扫描利用 Path 遍历 `cache_root / repo / **/SKILL.md`。选择交互用 `typer.prompt()` 或标准库 `input()` 实现编号选择。不引入第三方交互库。

## Decisions

### Command naming — enable/disable vs add/remove

**Question**: 新命令应该叫什么名字？
**Recommended**: enable/disable（项目已有术语）
**Chosen**: enable/disable
**Rationale**: FRD 和 V1 范围列表均使用 enable/disable 术语；MVP Non-Goals 明确 deferred 的是 enable/disable 交互命令

### Enable interface — interactive only

**Question**: `skill-manager enable` 应该接受什么参数？
**Recommended**: 三个参数 name/repo/path
**Chosen**: 交互式模式，无参数
**Rationale**: developer specified: "应该用交互式的方式，提供当前源中已有的仓库和里面的技能列表，让用户选择，最后回车一次性添加"

### Repository source — cached repos only

**Question**: enable 的可选仓库列表从哪里来？
**Recommended**: 已缓存的仓库
**Chosen**: 已缓存的仓库
**Rationale**: 即时可用、无须网络；新仓库由 sync 管理

### Disable interface — interactive only

**Question**: `skill-manager disable` 应该怎么工作？
**Recommended**: 按名称交互式选择
**Chosen**: 按名称交互式选择
**Rationale**: 与 enable 体验一致，列出已启用技能让用户选择

### Skill detection — SKILL.md scan

**Question**: 在选定的仓库中，如何识别哪些目录可以作为可用技能？
**Recommended**: 扫描含 SKILL.md 的目录
**Chosen**: 扫描含 SKILL.md 的目录
**Rationale**: 与 ensure_link 的 SKILL.md 校验逻辑一致，真实可靠

### Skill naming — auto from directory

**Question**: 用户选择技能目录后，name 字段怎么确定？
**Recommended**: 取目录名
**Chosen**: 取目录名
**Rationale**: 自动化，减少输入；根目录（`.`）技能取 repo 名转小写

### Duplicate handling — skip

**Question**: 如果用户 enable 一个已经启用的技能，应该怎么处理？
**Recommended**: 提示并跳过
**Chosen**: 提示并跳过
**Rationale**: 安全幂等，不做意外修改

### Disable cleanup — delete symlink

**Question**: disable 删除技能时，对 symlink 应该怎么处理？
**Recommended**: 删除 symlink
**Chosen**: 删除 symlink
**Rationale**: 完全清理，不留垃圾

### Auto-sync after modify

**Question**: enable/disable 修改配置后，是否自动执行 sync？
**Recommended**: 自动 sync
**Chosen**: 自动 sync
**Rationale**: 一步到位，用户不需要额外执行 sync

### Empty cache fallback

**Question**: 缓存仓库为空时 enable 怎么处理？
**Recommended**: 提示无可用仓库
**Chosen**: 提示无可用仓库
**Rationale**: 简单明确，建议先 sync

### Pre-resolved — need save_project_config

**Question**: 项目中是否存在 save_project_config 函数？
**Recommended**: No — only `load_project_config()` exists
**Chosen**: confirmed by codebase probe
**Rationale**: evidence: `src/skill_manager/config.py:115-131` only has `load_project_config`; `save_global_config` exists at `config.py:160-167`. A new `save_project_config()` must be added

### Pre-resolved — existing code structure

**Question**: 现有代码结构是否可作为 enable/disable 的基础？
**Recommended**: Yes — `cli.py` has `@app.command()` pattern, `config.py` handles JSON I/O, `sources.py` handles git operations, `links.py` handles symlinks
**Chosen**: confirmed by codebase probe
**Rationale**: all building blocks exist and can be reused; follow same error handling pattern (ConfigError/SourceError/LinkError → typer.Exit)

## Open Questions

None explicitly deferred.

## Suggested Follow-ups

- `enable <name>` 直接参数模式（非交互式快捷用法）：在交互式版本稳定后作为自然演进
- 优先显示已启用/可启用的区分标记：在仓库扫描结果中标注哪些技能已在本项目启用
- `skill-manager list` 扩展显示缓存仓库中全部可用技能：作为探索性功能

## References

- `.rpiv/artifacts/discover/2026-06-25_21-29-04_project-scoped-skill-manager.md` — 上游 FRD（MVP non-goals 明确 deferred enable/disable）
- `.rpiv/artifacts/plans/2026-06-27_22-35-47_skill-manager.md` — MVP 实现计划（"What We're NOT Doing: 不做 enable/disable 编辑命令"）
- `.rpiv/artifacts/validation/2026-06-30_22-03-43_project-scoped-declarative-skill-manager.md` — MVP 验证报告
- `src/skill_manager/cli.py` — 现有 CLI 结构和命令模式
- `src/skill_manager/config.py` — 数据模型和 JSON 读写（需新增 save_project_config）
- `src/skill_manager/links.py` — symlink 管理（ensure_link / LinkResult）
- `src/skill_manager/sources.py` — git 操作（ensure_source / repo_url）
