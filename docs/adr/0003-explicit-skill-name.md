# Skill 用显式 name 字段命名链接

曾考虑从 `path` 末段（或仓库名）派生链接名，但对仓库根目录技能（`path: "."`）会歧义，且同名技能跨仓库时无法消解。因此每条技能声明带显式 `name`，作为 `./.agents/skills/<name>` 的链接名；`name` 必须是单个路径分量，配置内重名直接报错。身份仍是 `name + repo + path`，`name` 只负责磁盘句柄，不替代 `repo + path` 的定位。
