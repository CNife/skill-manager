# 输出按 stdout 是否 TTY 分双轨：非 TTY 默认紧凑 JSON

`skill-manager` 有两个读者——终端前的人，和读 stdout 的 agent/脚本。旧实现只给一份「给眼睛看却不精致、给机器读又不规整」的手写文本：列表靠空格填充对齐，机器侧要显式 `--json`（且必须写在子命令之前），信封里还带 `": "` / `", "` 的装饰性空格。现在输出分两条轨，**判定单点 = `stdout.isatty()`**：TTY 得到 Human output（`render` 模块用 Rich 渲染的分节表格 + 行流 + 一行原地刷新的实时行），非 TTY 得到 JSON output（紧凑单行、无色、无进度行的单个 JSON 对象）。判定只看 stdout——不看 stderr，不认 `CI`，不认 `TERM`；实现上 Console 工厂的 `force_terminal` 与轨道标记由同一个 `isatty()` 推导，因此「判定」只有一个真相来源。

**为什么非 TTY 默认 JSON**：管道/重定向/CI 的 stdout 归属是机器。结构化数据配 `jq` 是正解，而「在管道里读人类文本」是伪需求——`less -R` 看漂亮表的场景不成立，`list | grep` 这类肌肉记忆该由 `| jq` 取代。代价明确且可接受：默认输出形态改变，`grep` 类脚本要改；当前 0.x、API 未冻结，此时做一次行为收敛成本最低。这同时消灭了「什么时候该加 `--json`」「旗标必须放在子命令前」这两件本该由工具自己判断的事，轨道边界还恰好卡在「转瞬信息」上：实时进度行只存在于 TTY，机器轨永远只有一个 JSON 对象，没有观众被亏待。

**为什么不保留 `--json`**：删旗标是决策的一部分，不是附带清理。若保留，它要么与默认行为冲突（非 TTY 已经是 JSON，旗标变为「加也白加」的僵尸），要么必须承诺「非 TTY 保持人类文本 + 加旗标才 JSON」，而那正是上面拒绝的伪需求。宁可让文档与实际只有一种说法。同理不引入 `--no-color` / `--text` / `--plain` / `--width` 逃生口：一旦有了开关，就永远有第二种真相。

**轨道内部边界**：元命令（`--help` / `--version` / `--show-completion`）不进任何轨，保持 click 原样——它们是 CLI 元信息，不是命令数据。JSON 轨的成功信封仍是 `{"ok":true,"data":…}`，失败是 `{"ok":false,"error":{"code","message"}}`，字段形状不变，退出码沿用 0/1/2（成功 / 域错误 / 用法错误）；用法错误在非 TTY 下也是同一个 JSON 信封，而不是 click 的 ASCII 帮助文本，这样解析失败不需要第二套读者。Human 轨的错误行写 stderr（红色 `✗  Error: …`），进度/空态写 stdout。颜色不再自研：TTY 轨交给 `Console` 自动判定，Rich 自己认 `NO_COLOR`（颜色消失，表格结构与加粗保留）与 `TERM=dumb`，`_color` / `_color_enabled` / `_ANSI_CODES` / `_STATUS_COLORS` 整体删除。写盘配置（`.skill-manager.json`、全局 config）仍是 `indent=2, sort_keys=True`——那是给人手改的文件，压紧是净损失。

**拒绝的替代方案**：①「非 TTY 保持人类文本，`--json` 继续存在」——见上，机器读者要么白付 token 读装饰，要么必须记住旗标；②「按 stderr 是否 TTY 判定」——stderr 是错误通道，用它决定数据格式会让 `2>/dev/null` 意外改轨；③「保留自研 ANSI，只重排布局」——颜色与结构会各有一套真相，且窄终端折行、宽字符对齐、`NO_COLOR` 全要自己实现；④「窄终端（< 80 列）退回扁平流」——不做特例，只依赖列宽策略（宽列 `overflow="fold"`、窄列 `no_wrap=True`）折行不丢字；实测 80 列及以上宽列完整折行、主键列（`name` / `status` / `repo`）从不裁成 `domain-modeli…`，更窄时被压缩的是宽列本身，若体验不可接受另开 issue。
