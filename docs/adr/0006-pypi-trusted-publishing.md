# 经 GitHub OIDC Trusted Publishing 发 PyPI

发布不使用长期 PyPI API token，而在 push `v*` tag 时由 `.github/workflows/publish.yml` 通过 OIDC 向 PyPI 换短时凭证上传。publisher 绑定仓库 `CNife/skill-manager`、workflow `publish.yml`、Environment `pypi`。相对本地 `uv publish` + token，可审计、无密钥落盘，且与 tag 驱动版本一致；首次需在 PyPI 控制台登记 pending publisher。
