# PyPI 分发名用 cnife-skill-manager，与 CLI 名分离

理想包名 `skill-manager` 因与已有 `skill-mgr` / `agent-skill-manager` 等过于相似，被 PyPI 拒绝登记。分发名改为 `cnife-skill-manager`；CLI 入口与 import 仍为 `skill-manager` / `skill_manager`，避免用户命令与代码路径被品牌前缀绑架。安装：`uv tool install cnife-skill-manager`，使用：`skill-manager`。
