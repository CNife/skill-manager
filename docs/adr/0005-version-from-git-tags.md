# 版本以 git tag + hatch-vcs 为唯一来源

包版本不手写在 `pyproject.toml` 或 `__init__.py`。发布时打 `v*` tag（如 `v0.1.0`），由 hatch-vcs 解析为 PEP 440 版本并写入 `_version.py`。这样 tag、PyPI 版本与运行时 `__version__` / `--version` 同一来源，避免多处漂移；相对静态写版本或 attr 读源码，更适合「打 tag 即发布」的 CI 流程。
