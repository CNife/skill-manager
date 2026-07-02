---
date: 2026-07-02T10:47:05+0800
author: CNife
commit: fc203a5
branch: main
repository: skill-manager
topic: "source repository management"
tags: [intent, frd, skill-manager, source, cli]
status: ready
last_updated: 2026-07-02T10:47:05+0800
last_updated_by: CNife
---

# FRD: Source Repository Management

## Summary

Add `skill-manager source` subcommand group (list / add / remove / update) that lets users explicitly manage source repositories — pre-clone repos not yet referenced by any project, check cache state and HEAD versions, pull updates, and clean up stale clones. Currently sources are only managed implicitly during `sync`; this feature exposes a first-class source management layer independent of project skill declarations.

## Problem & Intent

项目日常使用场景。当前系统中，source 仓库只在 `sync` 时被动派生和更新，用户无法独立管理源仓库层：

1. **查看**：想知道全局缓存里有哪些仓库、每仓库的 HEAD 版本、缓存是否完好。
2. **预添加**：想预先 clone 一个仓库到缓存，即使当前项目还没启用它——为后续 `enable` 做准备。
3. **更新**：想手动 pull 某个仓库的最新代码，而不执行完整的 `sync`（`sync` 还会做 symlink 操作）。
4. **清理**：想删除不再需要的缓存仓库，释放磁盘空间。

这些操作属于 4 层架构中的**第 1 层（技能来源层）**，与项目技能启用（第 3 层）正交。独立管理 source 层让工具职责更清晰、使用更灵活。

## Goals

- 用户可通过 `source list` 查看所有已注册源仓库的状态（HEAD commit、缓存存在性、URL）
- 用户可通过 `source add <repo>` 显式添加新源仓库（clone 到缓存 + 注册到全局配置）
- 用户可通过 `source remove <repo>` 删除源仓库（从全局配置移除 + 删除缓存目录）
- 用户可通过 `source update [repo]` 更新一个或全部源仓库（`git pull --ff-only` 到最新 HEAD，更新 HEAD 记录）
- 所有操作保持幂等、与现有 `sync`/`list` 流程一致
- 保持与现有 DRY 原则的兼容：`sync` 继续从 skills 派生 sources，不依赖显式 source 注册

## Non-Goals

- 不做非 GitHub 远端（第一版只认 `owner/repo`）
- 不做 `source prune` 自动清理（用户手动 `remove`）
- 不做 TUI / curses 交互式选择
- 不引入数据库、HTTP 服务器、GUI
- 不修改 `sync` 的行为（sync 继续从 skills 派生 sources）
- 不处理全局配置中 stale 的 source 条目（remove 时自然清理）

## Functional Requirements

### FR1: `skill-manager source list`

读取全局配置，列出所有已注册的 source 仓库及其状态：

```
  tw93/Waza       abc1234f  cached   https://github.com/tw93/Waza.git
  CNife/skills    def5678a  cached   https://github.com/CNife/skills.git
  x/y             -         missing  https://github.com/x/y.git
```

- 列出来源：全局配置 `sources` dict 中的所有 repo
- 每行显示：repo、HEAD 前 8 位（缺失显示 `-`）、缓存状态（`cached`/`missing`）、URL
- 缓存状态通过检查 `~/.cache/skill-manager/repos/<owner>/<repo>` 目录是否存在判断
- 无任何 source 时输出为空，退出 0

### FR2: `skill-manager source add <repo>`

添加并 clone 一个新的 source 仓库：

- `repo` 参数必填，格式 `owner/repo`，复用现有 `_validate_repo` 校验规则（安全 slug）
- 语义是"确保此 source 可用"：
  - repo 未注册 → clone + 注册到全局配置
  - 已注册且缓存存在 → 更新 HEAD（pull --ff-only），输出提示
  - 已注册但缓存缺失（人为删除等） → 重新 clone，刷新 HEAD
- 记录 HEAD commit 和 URL 到全局配置 `sources` dict
- clone 失败时抛 `SourceError`，退出非 0
- 不支持 `--url` 参数，URL 固定为 `https://github.com/<owner>/<repo>.git`

### FR3: `skill-manager source remove <repo>`

删除一个 source 仓库：

- 从全局配置 `sources` dict 移除条目
- 递归删除缓存目录 `~/.cache/skill-manager/repos/<owner>/<repo>`
- repo 不存在于全局配置时提示 `not found`，退出非 0
- 缓存目录不存在时静默继续（只移除配置条目）
- 如果当前项目配置中仍有技能引用此仓库，输出警告（非阻塞，继续执行删除）

### FR4: `skill-manager source update [repo]`

更新一个或全部 source 仓库到最新：

- 提供 `repo` 时只更新指定仓库；不提供时更新全局配置中所有仓库
- 对每个目标仓库执行 `git pull --ff-only`（复用 `ensure_source` 的 pull 分支）
- 如果缓存缺失（人为删除等），自动重新 clone 修复
- 更新全局配置中的 HEAD commit
- repo 未在全局配置中注册时报错退出非 0（提示先 `source add`）
- 每个仓库更新后输出其新 HEAD

### FR5: 错误处理

- repo 格式校验失败 → 报错退出非 0
- git 操作失败 → `SourceError`，CLI 捕获后退出非 0
- `source list` 始终退出 0（即使没有 source）
- 重复 `add` → 提示已存在，退出 0
- `remove` 不存在的 repo → 报错退出非 0

## Non-Functional Requirements

- **Performance**: 无特殊要求。git clone/pull 操作受网络速度支配
- **Security**: 只操作 XDG 缓存和全局配置；git over HTTPS 无凭据管理
- **UX**: 输出清晰，每行含 repo、HEAD、状态；`remove` 为显式操作的破坏性命令，作用域限定于单个 repo
- **Reliability**: `remove` 的目录删除操作前做路径安全检查（拒绝路径穿越）；`add` 的 clone 失败时不污染全局配置

## Constraints & Assumptions

- 实现语言：Python + typer，复用现有 `src/skill_manager/` 包结构
- 全局配置：`~/.config/skill-manager/config.json`
- 缓存目录：`~/.cache/skill-manager/repos/`
- GitHub 远端格式：仅 `owner/repo`
- 命令名：`skill-manager source <subcommand>`，子命令组模式
- 命令组内命令：list、add、remove、update
- `save_global_config` 负责持久化，复用现有函数

## Acceptance Criteria

- [ ] `skill-manager source list` 列出全部已注册 source 及其 HEAD 和缓存状态
- [ ] 无任何 source 时 `source list` 退出 0，输出为空
- [ ] `skill-manager source add tw93/Waza` clone 仓库并记录 HEAD
- [ ] 重复 `source add tw93/Waza` 已注册且缓存完好时提示已存在，退出 0
- [ ] `source add tw93/Waza` 已注册但缓存缺失时自动重新 clone
- [ ] `skill-manager source add invalid/repo/path` 报错退出非 0
- [ ] `skill-manager source remove tw93/Waza` 删除缓存目录和配置条目
- [ ] 移除不存在的 source 时 `source remove x/y` 报错退出非 0
- [ ] `skill-manager source update tw93/Waza` pull 指定仓库并更新 HEAD
- [ ] `skill-manager source update` 无参数时 pull 全部仓库
- [ ] `source update x/y` 当 x/y 未在全局配置注册时报错退出非 0
- [ ] `source update tw93/Waza` 已注册但缓存缺失时自动重新 clone
- [ ] `uv run pytest` 全部通过
- [ ] `ruff check .` 和 `ruff format --check .` 通过

## Recommended Approach

在 `cli.py` 中新增 `source_app = typer.Typer()` 子命令组，挂载到主 app。在 `sources.py` 中新增 `remove_source()` 函数。`source add` 和 `source update` 复用现有的 `ensure_source()`（缺失时 clone、存在时 pull --ff-only + 记录 HEAD；已注册但缓存缺失时自动修复）。`source list` 在 cli 层直接读取 `global_config.sources` 并检查缓存目录存在性。`source remove` 封装删除缓存目录 + 配置条目 + 可选的引用警告。repo 校验在 `config.py` 中提取公共 `validate_repo` 函数供 CLI 层调用。

## Decisions

### CLI shape — subcommand group vs flat commands

**Question**: source 操作应该用什么 CLI 形态？
**Recommended**: 子命令组 — `skill-manager source list / add / remove / update`
**Chosen**: 子命令组
**Rationale**: Typer 原生支持 `app.add_typer`，`source` 作为命名空间比扁平前缀更规范；与 `git remote`、`git stash` 等模式一致；interview 确认

### Update scope — all when no argument

**Question**: `source update` 不带参数时更新什么？
**Recommended**: 更新全部已注册的 source 仓库
**Chosen**: 更新全部
**Rationale**: 与 `git remote update` 同理，全量更新是最常用的场景；指定单个 repo 提供精确控制；interview 确认

### Remove safety — warn about project references

**Question**: `source remove` 时如果当前项目仍有技能引用该仓库，怎么处理？
**Recommended**: 警告但继续执行
**Chosen**: 警告但继续
**Rationale**: 用户显式执行 remove，信任用户判断；但提供有用信息避免意外破坏；interview 确认

### Add URL parameter — no --url flag

**Question**: `source add` 是否支持 `--url` 参数覆盖默认 URL？
**Recommended**: 不支持 `--url`，固定 `https://github.com/<owner>/<repo>.git`
**Chosen**: 不支持
**Rationale**: 保持简单；测试层面在 `sources.py` 已有 url 参数支持（通过 `ensure_source` 的 `url` 参数），CLI 不需暴露；interview 确认

### Pre-resolved — reuse ensure_source

**Question**: `add` 和 `update` 是否复用现有 `ensure_source` 函数？
**Recommended**: 复用 — `sources.py:33-56`
**Chosen**: 复用
**Rationale**: `ensure_source` 已封装 clone 和 pull --ff-only + HEAD 记录逻辑；`add` = clone 分支，`update` = pull 分支

### Pre-resolved — reuse GlobalConfig.sources

**Question**: `list` 和持久化是否复用现有 `GlobalConfig.sources` dict？
**Recommended**: 复用 — `config.py:30-39, 134-167`
**Chosen**: 复用
**Rationale**: 全局配置已用 dict 按 repo 索引、含 commit 和 url 字段；`load_global_config` / `save_global_config` 已实现

### Pre-resolved — CLI follows existing pattern

**Question**: CLI 代码结构是否沿用现有 `@app.command()` + error handling 模式？
**Recommended**: 沿用 — `cli.py:213-270`
**Chosen**: 沿用
**Rationale**: `sync`/`list`/`enable`/`disable` 均已使用同一模式；子命令组只需新增 `source_app` + `@source_app.command()`

### Pre-resolved — expose public validate_repo

**Question**: CLI 层如何校验 repo 格式？
**Recommended**: 将 `config._validate_repo` 提取为公共函数 `validate_repo(repo: str, context: str) -> None`，CLI 层导入公共 API
**Chosen**: 提取为公共函数
**Rationale**: 不应从 CLI 层导入私有函数（`_validate_repo`）；在 `config.py` 中暴露 `validate_repo`，保持与 `ConfigError` 一致的校验接口

## Open Questions

None — all decisions resolved in interview.

## Suggested Follow-ups

（无）

## References

- `.rpiv/artifacts/discover/2026-06-25_21-29-04_project-scoped-skill-manager.md` — 上游 FRD（4 层架构）
- `.rpiv/artifacts/discover/2026-07-02_09-31-42_interactive-enable-disable-commands.md` — enable/disable FRD（CLI pattern）
- `.rpiv/artifacts/plans/2026-06-27_22-35-47_skill-manager.md` — MVP 实现计划
- `src/skill_manager/cli.py` — 现有 CLI 结构
- `src/skill_manager/sources.py` — 现有 source git 操作（ensure_source）
- `src/skill_manager/config.py` — 数据模型和全局配置（GlobalConfig.sources）
