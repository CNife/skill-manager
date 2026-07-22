# Source 从技能声明派生

项目配置只声明 Skill（`name` + `repo` + `path`），不维护显式的 sources 列表。`sync` 从声明的 `repo` 字段去重派生需要的 Source。保持 DRY，避免「仓库已声明但无技能」与「技能已声明但仓库未注册」两套状态。独立的 `source add/remove/update` 管理全局缓存层，与项目声明正交：预克隆可以，但项目侧仍以声明为准派生。
