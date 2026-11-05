## Agent skills

### Language

- README 与 CLI 输出用英文（对外产品面）
- 开发协作（issue、ADR、agent 交流）用中文

### Delivery gate

本仓库接入了 no-mistakes 门禁（配置见 `.no-mistakes.yaml`）。

- 功能分支用 `git push no-mistakes <branch>` 推送；门禁会依次跑 review、test、document、lint，通过后自动推送并建 PR。
- 不要直接 `git push origin`：那会绕过门禁。
- 查看进度与门禁状态用 `no-mistakes axi status`；PR 仍需潭渊批准后合并。

### Issue tracker

Issues live as GitHub issues in this repo, via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five default triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
