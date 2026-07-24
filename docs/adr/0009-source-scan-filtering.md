# Source 扫描：默认过滤 + skill 根截断 + `--all`

集合类 Source（个人 skills monorepo 等）常把归档放在 `.archive/`，或用 `metadata.internal: true` 标记非公开 skill；无过滤的深度 `rglob` 会把这些与 `node_modules`/`dist` 噪声一并暴露给 `source available-skills` 和 `enable`。

**决策**：清单与 enable 解析共用同一扫描入口，默认 `include_all=false`：

1. 不进入 `node_modules` / `dist` / `build` / `__pycache__`
2. 不进入名称以 `.` 开头的目录
3. 不输出 `metadata.internal` 为 YAML 布尔 `true` 的 skill 根
4. 目录含 `SKILL.md` 即 skill 根：决定是否输出后**不再递归**（默认与 `--all` 均截断）

`--all` 关闭 1–3，不关闭 4。另有与过滤正交的**资格门槛**（交互 enable、非交互 enable 解析、`source available-skills` 共用）：`SKILL.md` 须 UTF-8 可解码，且 YAML frontmatter 含非空字符串 `name` 与 `description`；发现名 = FM `name`（不再用目录 basename / 仓库根小写特例），`path` 仍为相对 skill 目录（仓库根为 `"."`）。`--all` 不放宽资格门槛。已声明的 `path` 不经扫描过滤与资格门槛，`sync`/`list` 行为不变。

相对 pi（跳过点目录 + skill 根截断）对齐截断与点目录；额外尊重 `metadata.internal`（近 skills CLI 安装过滤），且**不**白名单 `skills/.curated` 一类点路径——比 skills CLI 更严，避免集合仓归档默认进菜单。未对齐任一方的完整策略，故用 `--all` 作显式逃生口。
