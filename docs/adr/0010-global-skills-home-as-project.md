# 全局技能管理：home 即全局项目 + `--global` 作用域

全局技能用与项目**相同**的声明+同步模型，通过根选项 `--global` 切换作用域，不另起一套命令或数据结构。

**作用域与路径**：

- 项目（默认）：声明 `./.skill-manager.json`，Link `./.agents/skills/`
- 全局（`--global`）：声明 `~/.skill-manager.json`，Link `~/.agents/skills/`
- 源注册表 `~/.config/skill-manager/config.json` 与源缓存 `~/.cache/skill-manager/repos/` **跨作用域共享**——Source 只克隆一份，项目与全局技能都引用它

**为什么是 home 即项目**：`~/.skill-manager.json` 恰是 `paths.project_config_path(Path.home())`，`~/.agents/skills/` 恰是 `paths.project_skills_dir(Path.home())`；`run_*` 早已路径参数化，全局模式 = 用 home 路径调同一套 `run_*`，零模型分叉。这是「One model, project and global」最忠实的落地。

**`--global` 作为根选项**：与 `--json` 同层，默认项目行为不变；二者可任意顺序组合（`--json --global` 与 `--global --json` 等价）。`source` 子命令不受 `--global` 影响（源本就全局共享），`--global source ...` 是无操作而非错误。

**有意边界**：从 `~` 运行无 `--global` 会命中 `~/.skill-manager.json`（home 即全局项目）——这是模型的一致结果，不加护栏，仅文档化。`paths.project_config_path()` 不向上查找，只在字面 cwd 取配置，故子目录运行不会误命中 home 配置。

**enable 对缺失声明容错**：首次 `--global enable` 时 `~/.skill-manager.json` 尚不存在；`enable` 将缺失文件视为空声明，`save` 时创建，使冷启动无需手建文件。`sync`/`list` 仍严格——缺项目配置仍意味着「不在项目中」（既有测试约束）。

**source remove 双声明警告**：源共享，移除一个 Source 可能同时打断项目与全局的 Link；`source remove` 的「仍被引用」警告扩展为检查项目+全局两份声明，json 模式下不打印警告但照常移除。

**命名**：原 `ProjectConfig` 重命名为 `SkillDeclarations`（作用域无关），`load/save_project_config` → `load/save_skill_declarations`。JSON 键仍为 `skills`，`SkillRef`/`GlobalConfig`/`Source` 不变。
