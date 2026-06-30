---
date: 2026-06-25T21:29:04+0800
author: CNife
commit: no-commit
branch: main
repository: skill-manager
topic: "project-scoped declarative skill manager"
tags: [intent, frd, skill-manager, project-skills]
status: ready
last_updated: 2026-06-25T21:29:04+0800
last_updated_by: CNife
---

# FRD: Project-scoped Declarative Skill Manager

## Summary
Build `skill-manager`, a small Python stdlib CLI that restores project-specific skill enablement from declarative JSON. The tool keeps GitHub skill source repositories in an XDG cache, records source state in XDG config, and links selected `repo + path` skills into the current project's `./.agents/skills/` directory so global skills can stay minimal.

## Problem & Intent
用户的原始意图：

> 目前来讲，我所有的技能基本上都是安装在全局的。这样就会出现一个问题：每当我安装一个技能时，我都会去想，这个技能是不是在每一个地方都需要。这就会导致很多时候，安装的技能并不是我需要的，全局安装的这些技能里面，绝大部分其实都不需要。最好的方法其实是：在不同的项目里使用不同的技能，这些技能之间各有各的需求，同时把全局的技能尽可能做得小。这样一种方式，能让 AI 更加专注于真正重要的技能，而不是什么技能都装在全局。

后续澄清：工具流程是读取当前项目的 `./.skill-manager.json`，把项目声明的仓库加入/刷新全局源配置，缺失时克隆到全局源缓存，再把仓库内指定技能软链接到项目默认 `./.agents/skills/`。

## Goals
- 让每个项目声明自己需要启用的技能，而非全部装在全局
- 工具从声明式配置文件恢复项目技能状态（sync 语义）
- 工具只做配置读取 → Git clone/fetch → symlink 操作，不引入数据库或服务
- 第一版给出 `sync` + `list` 两个命令的最小闭环

## Non-Goals
- 第一版不做交互式勾选 UI（curses/TUI）
- 第一版不做 `skill-manager enable/disable` 编辑命令（用户直接写 JSON）
- 不实现数据库、HTTP 服务器、GUI
- 不实现 Pi 扩展（不监听目录切换事件）
- 不竞争或取代 Vercel `skills` 的安装机制
- 不支持非 GitHub 的 Git 远端（第一版只认 `owner/repo`）

## Functional Requirements

### 完整工具能力
1. 管理系统从哪些 GitHub 仓库获取技能（sources）
2. 在全局缓存中 clone/fetch 技能源仓库，记录仓库与最新 commit HEAD
3. 全局层面记录已启用技能：每个技能记录 `source repo + 相对路径`
4. 每个项目独立记录启用技能列表
5. 将启用的技能暴露到项目目录下（symlink）
6. 检测断链、缺失 SKILL.md、配置声明但未安装等不一致状态
7. 从配置文件恢复完整状态（sync）

### 第一版 MVP 能力（`sync` + `list`）
1. `skill-manager sync` 读取当前项目 `./.skill-manager.json`：
   - 把项目声明的 `owner/repo` 写入全局配置的 sources 段（若不存在）
   - 确保全局源缓存中有该仓库的 clone（缺失则 clone，已存在则 fetch）
   - 将仓库 HEAD 写入全局配置 sources 段的 commit 字段
   - 在项目 `./.agents/skills/` 下为每个启用的技能创建 symlink，指向源仓库内对应路径的 `SKILL.md` 所在目录
   - 目标目录已存在非本工具创建的 symlink 时跳过而非覆盖
   - 源仓库中找不到指定路径时报错退出非 0
   - 整体是幂等的：重复执行不产生副作用
2. `skill-manager list` 读取当前项目 `.skill-manager.json` 并输出：
   - 来源仓库列表
   - 启用的技能列表（repo + path），每个技能标注当前仓库目录是否存在、symlink 是否有效
3. 全局配置文件缺省目录时自动创建

## Non-Functional Requirements
- **Performance**: 无特殊要求。sync 受 Git clone/fetch 速度支配，属于可接受范围
- **Security**: 不存储密钥，Git clone 通过 HTTPS；不操作不受信任的用户输入以外的系统区域
- **UX / Accessibility**: 命令输出清晰，错误信息包含具体路径和原因，退出码表明成功/失败
- **Reliability**: symlink 操作在目标目录非本工具管理时跳过；配置 JSON 格式错误时报错并退出非 0

## Constraints & Assumptions
- 实现语言：Python + typer（依赖 click），由 uv 管理项目依赖和运行环境
- 运行环境：Python 3.11+（有 `tomllib`，虽然本 FRD 选 JSON，但 Python 3.11+ 是最低门槛）
- 代码库基线：`/home/cnife/code/skill-manager` 当前为空仓库，无代码库先例
- 命令名：`skill-manager`（不接受缩写 `sk`）
- GitHub 远端格式：仅 `owner/repo`，工具推导为 `https://github.com/owner/repo.git`
- 项目默认技能链接目录：`./.agents/skills/`
- 用户通过 dotfiles 手动同步 `~/.config/skill-manager/` 来实现换机恢复
- 项目级配置格式为 JSON（用户指定文件名 `./.skill-manager.json`）
- 全局配置为 JSON（与项目格式保持一致，默认路径 `~/.config/skill-manager/config.json`）
- 全局源缓存目录 `~/.cache/skill-manager/repos/`

## Acceptance Criteria
- [ ] 在含有效 `./.skill-manager.json` 的项目中运行 `skill-manager sync`，缺失的源仓库被 clone 到 `~/.cache/skill-manager/repos/`，启用的技能被 symlink 到 `./.agents/skills/` 下
- [ ] 再次运行 `skill-manager sync` 不产生额外 clone 或 symlink 重复创建，退出码 0
- [ ] `skill-manager list` 输出列出 sources 和启用的技能，标注状态
- [ ] `./.skill-manager.json` 格式错误时 `skill-manager sync` 报错退出非 0
- [ ] JSON 中指定不存在的仓库路径时，`skill-manager sync` 报错退出非 0（不出于 non-zero exit code）
- [ ] `sync` 完成后，`ls -l ./.agents/skills/` 下每个技能是指向源仓库目录的有效 symlink
- [ ] `./.agents/skills/` 下已有非本工具创建的 symlink 时，sync 跳过不覆盖

## Recommended Approach
Python + typer CLI under `src/skill_manager/` layout, managed by uv. `skill-manager sync` reads `./.skill-manager.json`, reconciles sources in `~/.config/skill-manager/config.json`, clones/fetches repos in `~/.cache/skill-manager/repos/`, creates symlinks in `./.agents/skills/`. Uses `typer` for CLI, `subprocess` for git operations, `os.symlink` / `pathlib` for link management. No wrapper script, no compiled binary, no daemon.

## Decisions

### Intent — who hits this problem
**Question**: 最先要解决的是谁在什么场景下的痛点？
**Recommended**: n/a — intent question
**Chosen**: 用户自己在不同项目切换时遇到的问题是，全局技能太多、不需要的也全程加载，希望把全局做小、按项目启用
**Rationale**: developer's own words, captured verbatim

### Complete tool — functional scope
**Question**: 完整工具需要覆盖哪些功能族？
**Recommended**: sources management, global library, per-project enablement, health checking
**Chosen**: 完整 4 层，但第一版做 sync/list 最小闭环
**Rationale**: user clarified with layer-by-layer breakdown after rejecting multi-select framing

### Skill identity — how skills are referenced
**Question**: 配置里记录技能时应该用哪种身份标识？
**Recommended**: repo + path
**Chosen**: repo + path
**Rationale**: optimized for uniqueness and simplicity, avoids alias management overhead

### Exposure — how skills reach the project
**Question**: 项目启用时，工具应该把技能如何暴露给 Pi？
**Recommended**: 创建项目 symlink
**Chosen**: 工具克隆缺失源仓库到全局缓存，然后软链接到 `./.agents/skills/`
**Rationale**: developer had a specific flow in mind, confirmed during interview

### Sync scope — what `sync` restores
**Question**: `skill-manager sync` 的最小职责应该是什么？
**Recommended**: 按当前项目恢复
**Chosen**: 按当前项目恢复
**Rationale**: sync reads project config, ensures sources + links; commands named `skill-manager` not `sk`

### Path convention — global config and cache
**Question**: 全局配置和源仓库缓存默认放在哪里？
**Recommended**: XDG config/cache
**Chosen**: XDG config/cache
**Rationale**: follows Linux convention, friendly to dotfiles sync

### Project config — location
**Question**: 项目自己的启用配置默认文件名应该是什么？
**Recommended**: `.skill-manager.toml`
**Chosen**: `./.skill-manager.json` (JSON)
**Rationale**: developer explicitly specified JSON format over TOML; inferred global config also JSON for consistency

### MVP boundary — first version scope
**Question**: 第一版的边界应该怎么切？
**Recommended**: sync/list 最小闭环
**Chosen**: sync/list 最小闭环
**Rationale**: covers core restore+inspect without interactive editing or health commands

### Language/runtime
**Question**: 实现语言/运行形态怎么定？
**Recommended**: Python stdlib CLI
**Chosen**: Python + typer（依赖 click），uv 管理
**Rationale**: typer 提供声明式子命令和 --help 生成，优于手写 argparse；uv 替代 pip 管理包和运行环境

### Safety defaults
**Question**: 这些独立细节先怎么定？
**Recommended**: 保守安全
**Chosen**: 保守安全
**Rationale**: non-tool symlinks preserved, HEAD recorded, missing/conflict → non-zero exit

### Repo identifier in sources
**Question**: 全局 source 里的 GitHub 仓库 ID 默认怎么写？
**Recommended**: owner/repo
**Chosen**: owner/repo
**Rationale**: short, tool derives HTTPS URL automatically; non-GitHub deferred

### Acceptance criteria priority
**Question**: 第一版完成时最关键的验收条件应该是哪组？
**Recommended**: 真实项目可恢复
**Chosen**: 真实项目可恢复
**Rationale**: sync+list in a project with real `.skill-manager.json` covers the core workflow

### Codebase baseline — pre-resolved
**Question**: 当前代码库是否存在可复用结构？
**Recommended**: No — `/home/cnife/code/skill-manager` is empty (only `.git/`)
**Chosen**: confirmed by file probe
**Rationale**: evidence: `fd -a -H -t f . /home/cnife/code/skill-manager` returned only `.git/` internals

### Project structure
**Question**: 项目目录布局和打包方式？
**Recommended**: src 布局 + hatchling
**Chosen**: `src/skill_manager/` layout, pyproject.toml with hatchling build backend, console_scripts entry point `skill-manager`
**Rationale**: src 布局避免导入混淆，hatchling 是 pyproject.toml 原生构建后端，配置最简

### CLI design
**Question**: CLI 入口和子命令组织方式？
**Recommended**: argparse + 函数分发
**Chosen**: typer + subcommand（`skill-manager sync`, `skill-manager list`）
**Rationale**: typer 基于 type hints 自动生成 --help 和子命令路由，远比手写 argparse 简洁

### Testing framework
**Question**: 测试框架用什么？
**Recommended**: pytest
**Chosen**: pytest（uv 管理，不额外配置 tox/nox）
**Rationale**: pytest 的 tmp_path fixture 天然适合测试文件/目录操作，subprocess 调用 CLI

### Code style & CI
**Question**: 代码风格与质量门禁？
**Recommended**: ruff 格式化 + 检查
**Chosen**: ruff (format + check) + pre-commit 钩子
**Rationale**: ruff 单一工具覆盖 format/lint，pre-commit 在 git commit 前自动拦截不合规代码；暂不配 GitHub Actions

## Open Questions
None explicitly deferred.

## Suggested Follow-ups
- Interactive `enable/disable` subcommands: observed as natural UX evolution beyond MVP
- `skill-manager check` for health verification (broken symlinks, missing SKILL.md): observed during interview as maintenance layer
- Conflict detection across sources sharing the same skill directory name
- Pi extension integration (dynamic skill reload on project switch): mentioned in codebase probe of `pi-extensions` pattern

## References
- `/tmp/skills-handoff.Cnzz7H/handoff.md` — prior session handoff with full problem analysis and tool comparison
- `~/.agents/.skill-lock.json` — Vercel skills v3 lock file, referenced as existing global skill metadata source
- `~/.pi/agent/npm/node_modules/@oh-my-pi/coding-agent/src/config/settings-schema.ts` — oh-my-pi `SkillsSettings` with `ignoredSkills`/`includeSkills` fields (exploratory probe finding, not consumed by MVP)
